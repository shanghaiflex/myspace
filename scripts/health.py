#!/usr/bin/env python3
"""Apple Health data for the site: samples from the Health Bridge iOS app land in health.db (SQLite, stdlib only),
and an hourly Claude review turns them into a short note for the home page.

The iPhone app posts HealthKit batches (see healthbridge/openapi.yaml) to serve.py:
  POST /v1/ingest/health/workouts | sleep | metrics   Authorization: Bearer <token from HEALTH_TOKENS>
Every sample carries its HealthKit UUID, so re-sends are idempotent and deletions are soft (deleted_at).

  python3 scripts/health.py digest [--days 7]        # text digest of the last N days (what the review model reads)
  python3 scripts/health.py pending                  # exit 0 if samples arrived since the last review, else 1
  python3 scripts/health.py review-save --model opus --file note.txt [--digest-file d.txt]
  python3 scripts/health.py reviews [--limit 10]     # past notes
  python3 scripts/health.py summary                  # JSON used by /api/health
  python3 scripts/health.py ingest <workouts|sleep|metrics> <batch.json>   # manual import (same JSON the app sends)
  python3 scripts/health.py stats                    # row counts per kind
  python3 scripts/health.py backup [--keep 7]        # snapshot health.db into backups/ (the phone is the only other copy)

Env: HEALTH_DB (default health.db next to serve.py), HEALTH_TZ (default WEATHER_TZ, then Europe/Moscow),
HEALTH_BACKUP_DIR (default backups/ next to health.db).
"""
import argparse, datetime as dt, json, os, re, sqlite3, sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("HEALTH_DB") or os.path.join(ROOT, "health.db")
BACKUP_DIR = os.environ.get("HEALTH_BACKUP_DIR") or os.path.join(ROOT, "backups")
METRIC_KINDS = ("hrv_sdnn", "resting_heart_rate", "steps", "active_energy")
NO_WATCH_KCAL = 50   # below this, with no sleep and no HRV, the watch simply was not on the wrist that day
MORNING_HOUR = 6     # local hour that starts a new day for the note shown on the page
WORKOUT_RU = {"running": "бег", "cycling": "велосипед", "walking": "ходьба", "swimming": "плавание", "yoga": "йога",
              "functionalStrengthTraining": "силовая", "functional_strength_training": "силовая", "traditionalStrengthTraining": "силовая",
              "hiking": "поход", "elliptical": "эллипс", "rowing": "гребля", "coreTraining": "кор", "other": "другое",
              "tennis": "теннис", "tableTennis": "настольный теннис", "crossTraining": "кросс-тренинг", "pilates": "пилатес",
              "highIntensityIntervalTraining": "HIIT", "swimBikeRun": "триатлон", "mixedCardio": "кардио", "dance": "танцы",
              "stairClimbing": "лестница", "climbing": "скалолазание", "boxing": "бокс", "martialArts": "единоборства",
              "flexibility": "растяжка", "cooldown": "заминка", "preparationAndRecovery": "разминка", "jumpRope": "скакалка",
              "basketball": "баскетбол", "soccer": "футбол", "volleyball": "волейбол", "badminton": "бадминтон",
              "skatingSports": "коньки", "snowSports": "лыжи", "crossCountrySkiing": "беговые лыжи", "downhillSkiing": "горные лыжи",
              "waterFitness": "аквафитнес", "surfingSports": "сёрфинг", "paddleSports": "гребля на каяке", "golf": "гольф",
              "mindAndBody": "тело и дыхание", "taiChi": "тайцзи", "barre": "барре", "stepTraining": "степ", "play": "игра"}
# The app translates only a handful of HealthKit types (HealthKitManager.swift) and sends the rest verbatim as
# "HKWorkoutActivityType(rawValue: 48)". Half of what is actually trained — tennis, силовая, кор, HIIT, триатлон —
# reached the note as a rawValue, so it could neither name a workout nor notice that one kind of them is missing.
HK_RAW = {3: "australianFootball", 6: "basketball", 8: "boxing", 9: "climbing", 11: "crossTraining", 13: "cycling",
          14: "dance", 16: "elliptical", 20: "functionalStrengthTraining", 21: "golf", 24: "hiking", 28: "martialArts",
          29: "mindAndBody", 31: "paddleSports", 32: "play", 33: "preparationAndRecovery", 35: "rowing", 37: "running",
          39: "skatingSports", 40: "snowSports", 41: "soccer", 44: "stairClimbing", 45: "surfingSports", 46: "swimming",
          47: "tableTennis", 48: "tennis", 50: "traditionalStrengthTraining", 51: "volleyball", 52: "walking",
          53: "waterFitness", 57: "yoga", 58: "barre", 59: "coreTraining", 60: "crossCountrySkiing", 61: "downhillSkiing",
          62: "flexibility", 63: "highIntensityIntervalTraining", 64: "jumpRope", 66: "pilates", 69: "stepTraining",
          72: "taiChi", 73: "mixedCardio", 80: "cooldown", 82: "swimBikeRun", 3000: "other"}
DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, kind TEXT NOT NULL,
  start TEXT NOT NULL, "end" TEXT NOT NULL, value REAL, unit TEXT,
  data TEXT NOT NULL, ingested_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_samples_kind_start ON samples(type, kind, start);
CREATE INDEX IF NOT EXISTS idx_samples_ingested ON samples(ingested_at);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, model TEXT, text TEXT NOT NULL,
  digest TEXT, samples_until TEXT);
"""


# ---------------------------------------------------------------- db
def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def tz():
    return ZoneInfo(os.environ.get("HEALTH_TZ") or os.environ.get("WEATHER_TZ") or "Europe/Moscow")


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    """RFC3339 from the app (Z or offset, optional fraction) → aware UTC datetime."""
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def local(s):
    return parse_ts(s).astimezone(tz())


# ---------------------------------------------------------------- ingest
def ingest(sample_type, batch):
    """sample_type: workouts | sleep | metrics. batch: {"items": [...], "deleted": [...]} as sent by the app."""
    if sample_type not in ("workouts", "sleep", "metrics"):
        raise ValueError("unknown sample type " + sample_type)
    items, deleted = batch.get("items") or [], batch.get("deleted") or []
    now = iso(utcnow())
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "skipped": 0}
    db = connect()
    with db:
        for it in items:
            try:
                if sample_type == "workouts":
                    row = ("workout", str(it["workoutType"]), float(it["durationMinutes"]), "min")
                elif sample_type == "sleep":
                    # Since 2026-09-14 the app sends HealthKit's stage samples as they are (kind = rem/deep/core/
                    # awake/inBed, one stretch each); older builds sent one summary per day under the kind "sleep".
                    row = (("sleep", str(it["stage"]), float(it["minutes"]), "min") if it.get("stage")
                           else ("sleep", "sleep", float(it["totalMinutes"]), "min"))
                else:
                    row = ("metric", str(it["kind"]), float(it["value"]), str(it.get("unit") or ""))
                sid, start, end = str(it["id"]), iso(parse_ts(it["start"])), iso(parse_ts(it["end"]))
            except (KeyError, ValueError, TypeError):
                stats["skipped"] += 1
                continue
            cur = db.execute("SELECT 1 FROM samples WHERE id=?", (sid,)).fetchone()
            db.execute("""INSERT INTO samples(id,type,kind,start,"end",value,unit,data,ingested_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,NULL)
                          ON CONFLICT(id) DO UPDATE SET type=excluded.type, kind=excluded.kind, start=excluded.start, "end"=excluded."end",
                          value=excluded.value, unit=excluded.unit, data=excluded.data, ingested_at=excluded.ingested_at, deleted_at=NULL""",
                       (sid, *row[:2], start, end, row[2], row[3], json.dumps(it, ensure_ascii=False), now))
            stats["updated" if cur else "inserted"] += 1
        for d in deleted:
            sid = str(d.get("id") or "")
            if sid and db.execute("UPDATE samples SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (now, sid)).rowcount:
                stats["deleted"] += 1
    return stats


# ---------------------------------------------------------------- backup
def backup(keep=7, db=None):
    """Snapshot health.db into BACKUP_DIR, keeping the last `keep` days. The samples only exist here and on the
    phone, and the phone re-sends nothing it has already delivered (anchors in UserDefaults), so this is the copy
    that survives the database being lost."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, "health-%s.db" % dt.datetime.now(tz()).strftime("%Y-%m-%d"))
    src = db or connect()
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)  # online backup: consistent even while serve.py is writing
    out.close()
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("health-") and f.endswith(".db"))
    dropped = old[:-keep] if keep > 0 else []
    for f in dropped:
        os.remove(os.path.join(BACKUP_DIR, f))
    return {"file": dest, "bytes": os.path.getsize(dest),
            "rows": src.execute("SELECT COUNT(*) c FROM samples").fetchone()["c"],
            "kept": len(old) - len(dropped), "dropped": dropped}


# ---------------------------------------------------------------- aggregation
def rows(db, since_utc, type_=None, kind=None):
    q, args = "SELECT * FROM samples WHERE deleted_at IS NULL AND \"end\" >= ?", [iso(since_utc)]
    if type_:
        q += " AND type=?"; args.append(type_)
    if kind:
        q += " AND kind=?"; args.append(kind)
    return db.execute(q + " ORDER BY start", args).fetchall()


def lane_sum(samples):
    """Cumulative metrics (steps, kcal) arrive from both the watch and the phone as overlapping intervals; HealthKit itself
    picks one source per moment. Approximate that: pack intervals into non-overlapping lanes and take the biggest lane."""
    lanes = []  # [end, total]
    for s in sorted(samples, key=lambda r: r["start"]):
        for lane in lanes:
            if lane[0] <= s["start"]:
                lane[0], lane[1] = s["end"], lane[1] + (s["value"] or 0)
                break
        else:
            lanes.append([s["end"], s["value"] or 0])
    return max((l[1] for l in lanes), default=0)


def workout_kind(kind):
    """Canonical name for a workout type the app did not translate (see HK_RAW)."""
    m = re.fullmatch(r"HKWorkoutActivityType\(rawValue: (\d+)\)", (kind or "").strip())
    return HK_RAW.get(int(m.group(1)), kind) if m else kind


def sleep_stretch(s):
    """How much longer the payload's interval is than the sleep inside it. A record of one stretch of sleep is
    tight (the two agree to a minute); a payload the app rebuilt around several nights covers a whole day."""
    return (parse_ts(s["end"]) - parse_ts(s["start"])).total_seconds() / 60 - (s["value"] or 0)


def merge_sleep(samples):
    """One night can sit in the database several times over. The app groups a night's stages into a single payload
    with a fresh UUID on every sync (SleepAssembler), so a re-sync after a reinstall stores the same night again,
    and a night later regrouped together with the next evening's dozing arrives as one interval covering both.
    Summed blindly that gave 20 hours of sleep a night and a 7-day average of 17. Cluster the payloads by
    overlapping time and keep the tightest of each cluster — the copy that describes one stretch of sleep and
    nothing else, which also throws away the day-long regroupings. Stretches that really are separate (a nap, an
    awakening in the middle of the night) do not overlap and all of them are kept."""
    clusters = []  # [cluster end, tightest sample]
    for s in sorted(samples, key=lambda r: (r["start"], r["end"])):
        if clusters and s["start"] < clusters[-1][0]:
            c = clusters[-1]
            c[0] = max(c[0], s["end"])
            if (sleep_stretch(s), -(s["value"] or 0)) < (sleep_stretch(c[1]), -(c[1]["value"] or 0)):
                c[1] = s
        else:
            clusters.append([s["end"], s])
    return [c[1] for c in clusters]


def union_minutes(intervals):
    """Length of the union of time intervals. Two sources (watch and phone) can describe the same stretch of
    sleep at once; adding them up would double it."""
    total, cur = 0.0, None
    for s, e in sorted(intervals):
        if cur is None or s > cur[1]:
            if cur:
                total += (cur[1] - cur[0]).total_seconds() / 60
            cur = [s, e]
        else:
            cur[1] = max(cur[1], e)
    return total + ((cur[1] - cur[0]).total_seconds() / 60 if cur else 0)


def night_from_stages(ss):
    """One night out of HealthKit's own stage samples: asleep is rem + deep + core, the minutes awake in bed are
    not sleep, and a phone without a watch only ever knows «in bed»."""
    def mins(kinds):
        return union_minutes([(parse_ts(r["start"]), parse_ts(r["end"])) for r in ss if r["kind"] in kinds])
    asleep = [r for r in ss if r["kind"] in ASLEEP_STAGES]
    span = asleep or [r for r in ss if r["kind"] == "inBed"]
    if not span:
        return None
    return {"start": min(r["start"] for r in span), "end": max(r["end"] for r in span),
            "total": mins(ASLEEP_STAGES) or mins(("inBed",)), "deep": mins(("deep",)), "rem": mins(("rem",)),
            "core": mins(("core", "asleep")), "awake": mins(("awake",)), "sessions": 1}


def stage_nights(ss):
    """Stage samples grouped into nights: stretches less than SLEEP_GAP apart are one night, so waking at four and
    falling asleep again is still one night, while an afternoon nap is its own."""
    nights, cur, cur_end = [], [], None
    for r in sorted(ss, key=lambda r: r["start"]):
        if cur and parse_ts(r["start"]) - cur_end > dt.timedelta(minutes=SLEEP_GAP):
            nights.append(cur); cur = []
        cur.append(r)
        end = parse_ts(r["end"])
        cur_end = end if cur_end is None or len(cur) == 1 else max(cur_end, end)
    if cur:
        nights.append(cur)
    return [n for n in (night_from_stages(c) for c in nights) if n]


def legacy_night(s):
    """A night as the app used to send it: one payload per local day with the stages already added up."""
    b = json.loads(s["data"]).get("breakdown") or {}
    awake = b.get("awakeMinutes") or 0
    return {"start": s["start"], "end": s["end"], "total": (s["value"] or 0) - awake, "deep": b.get("deepMinutes") or 0,
            "rem": b.get("remMinutes") or 0, "core": b.get("coreMinutes") or 0, "awake": awake, "sessions": 1}


def nights(ss):
    """Every night the database knows about, newest format first. The app sent day-long summaries until 2026-09-14
    and sends the stage samples themselves since; a summary is used only where no stage sample covers it, so the
    two never count the same night twice."""
    out = stage_nights([r for r in ss if r["kind"] != "sleep"])
    covered = [(n["start"], n["end"]) for n in out]
    # The old payloads are compared only within the morning they belong to: one of them can span a whole day and
    # would otherwise bridge two separate nights into a single cluster and swallow one of them.
    legacy = {}
    for r in ss:
        if r["kind"] == "sleep":
            legacy.setdefault(sleep_key(r["end"]), []).append(r)
    for group in legacy.values():
        kept = merge_sleep(group)
        # A payload stretched over a whole day holds the previous night as well as this one and there is no way to
        # tell them apart; where the same morning also has a payload describing one stretch of sleep, trust that one.
        tight = [s for s in kept if sleep_stretch(s) <= SLEEP_STRETCH_MAX]
        for s in (tight or kept):
            if not any(s["start"] < e and b < s["end"] for b, e in covered):
                out.append(legacy_night(s))
    return out


def day_key(s):
    return local(s).strftime("%Y-%m-%d")


SLEEP_EVENING = 18   # a stretch of sleep ending at or after this local hour belongs to the night now beginning
SLEEP_GAP = 180      # minutes between two stretches of sleep that still count as one night
SLEEP_STRETCH_MAX = 120   # minutes a payload may exceed the sleep inside it before it is a regrouping, not a night
ASLEEP_STAGES = ("rem", "deep", "core", "asleep")


def sleep_key(s):
    """The morning a stretch of sleep counts towards. Falling asleep at 22:40 and waking at 23:55 is not a nap and
    not a night of its own: it is the first hour of the night that ends tomorrow morning, and the app files it
    under today because that is the day it ended. Counted as its own night it read as «сон 1ч17»."""
    end = local(s)
    return (end.date() + dt.timedelta(days=1 if end.hour >= SLEEP_EVENING else 0)).strftime("%Y-%m-%d")


def daily(db, days, today=None):
    """Per-day table for the last `days` local days (newest first). Sleep counts on the morning it ended."""
    today = today or dt.datetime.now(tz()).date()
    first = today - dt.timedelta(days=days - 1)
    since = dt.datetime.combine(first, dt.time(0), tz()) - dt.timedelta(hours=12)  # sleep that started the evening before
    out = {}
    for i in range(days):
        d = first + dt.timedelta(days=i)
        out[d.strftime("%Y-%m-%d")] = {"date": d.strftime("%Y-%m-%d"), "dow": DOW[d.weekday()], "sleep": None, "rhr": None,
                                      "hrv": None, "hrvN": 0, "steps": None, "kcal": None, "workouts": []}
    buckets, sleep_rows = {}, []
    for r in rows(db, since):
        if r["type"] == "sleep":
            sleep_rows.append(r)
        elif day_key(r["start"]) in out:
            buckets.setdefault((day_key(r["start"]), r["type"], r["kind"]), []).append(r)
    for n in nights(sleep_rows):
        key = sleep_key(n["end"])
        if key not in out:
            continue
        d = out[key]
        tot = d["sleep"] or {"total": 0, "deep": 0, "rem": 0, "core": 0, "awake": 0, "sessions": 0, "main": 0, "last": ""}
        for k in ("total", "deep", "rem", "core", "awake"):
            tot[k] += n[k]
        tot["sessions"] += 1
        tot["last"] = max(tot["last"], n["end"])
        if n["total"] >= tot["main"]:   # bed and wake describe the main stretch, not the dozing around it
            tot["main"] = n["total"]
            tot["bed"] = local(n["start"]).strftime("%H:%M"); tot["wake"] = local(n["end"]).strftime("%H:%M")
        d["sleep"] = tot
    for d in out.values():
        if d["sleep"]:
            # A night ends in the morning. If everything recorded for this one ends late in the evening, the record
            # breaks off where sleep went on — the watch stopped writing, or the app filed the rest under the next
            # day — and the hour and a half before midnight is not a night to report as «спал 1ч24».
            if local(d["sleep"]["last"]).hour >= SLEEP_EVENING:
                d["sleep"] = None
                continue
            d["sleep"] = {k: (round(v) if isinstance(v, float) else v) for k, v in d["sleep"].items() if k not in ("main", "last")}
    for (key, type_, kind), ss in buckets.items():
        d = out[key]
        if type_ == "workout":
            for s in ss:
                raw = json.loads(s["data"])
                k = workout_kind(kind)
                d["workouts"].append({"type": k, "ru": WORKOUT_RU.get(k, k), "start": local(s["start"]).strftime("%H:%M"),
                                      "min": round(s["value"] or 0), "km": round((raw.get("distanceMeters") or 0) / 1000, 2) or None,
                                      "kcal": round(raw["calories"]) if raw.get("calories") else None,
                                      "hr": round(raw["averageHeartRate"]) if raw.get("averageHeartRate") else None})
        elif kind == "resting_heart_rate":
            d["rhr"] = round(sum(s["value"] for s in ss) / len(ss))
        elif kind == "hrv_sdnn":
            d["hrv"] = round(sum(s["value"] for s in ss) / len(ss)); d["hrvN"] = len(ss)
        elif kind == "steps":
            d["steps"] = round(lane_sum(ss))
        elif kind == "active_energy":
            d["kcal"] = round(lane_sum(ss))
    for d in out.values():
        d["workouts"].sort(key=lambda w: w["start"])
        # The watch is not worn every day. Sleep, HRV and resting heart rate come only from it, and without it
        # active energy falls to the handful of kilocalories the phone estimates. Such a day is a hole in the
        # record, not a health event — it must not be read as an anomaly nor averaged in with the rest.
        # Only a day the phone did record counts: a day with nothing at all is an empty day, not a bare wrist.
        recorded = d["steps"] is not None or d["kcal"] is not None or d["workouts"]
        d["noWatch"] = bool(recorded) and d["sleep"] is None and d["hrv"] is None and (d["kcal"] or 0) < NO_WATCH_KCAL
    return list(out.values())[::-1]


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals)) if vals else None


def hm(minutes):
    if minutes is None:
        return "—"
    return f"{int(minutes) // 60}ч{int(minutes) % 60:02d}"


def last_ingest(db):
    r = db.execute("SELECT MAX(ingested_at) m FROM samples").fetchone()
    return r["m"] if r else None


def last_review(db):
    return db.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 1").fetchone()


def fmt_local(s):
    return local(s).strftime("%d.%m %H:%M") if s else "—"


def daybreak(now=None):
    """Start of the current day for the note: today at MORNING_HOUR, or yesterday's if it is still night."""
    now = now or dt.datetime.now(tz())
    mark = now.replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    return mark if now >= mark else mark - dt.timedelta(days=1)


def digest(db=None, days=7):
    """Human-readable digest for the review model: daily table, averages, workouts, freshness."""
    db = db or connect()
    now = dt.datetime.now(tz())
    li = last_ingest(db)
    n_all = db.execute("SELECT COUNT(*) c FROM samples WHERE deleted_at IS NULL").fetchone()["c"]
    lines = [f"Сейчас: {now.strftime('%Y-%m-%d')} ({DOW[now.weekday()]}) {now.strftime('%H:%M')} {tz().key}",
             f"Последние данные с телефона: {fmt_local(li)}" + (f" ({int((utcnow() - parse_ts(li)).total_seconds() // 60)} мин назад)" if li else "") + f", всего образцов в базе: {n_all}", ""]
    table = daily(db, days, now.date())
    lines.append("Дни (сон засчитан на утро пробуждения; шаги/ккал приблизительно, если часы и телефон считали одновременно).")
    lines.append("Пометка «без часов» = часы в этот день не носились: сна, HRV и калорий там нет не потому, что что-то не так.")
    lines.append("дата       сон    глуб/REM    отбой–подъём  RHR  HRV(n)   шаги   ккал  тренировки")
    for d in table:
        s = d["sleep"]
        sl = f"{hm(s['total']):6} {hm(s['deep'])}/{hm(s['rem'])}  {s['bed']}–{s['wake']}" if s else f"{'—':6} {'—':10} {'—':12}"
        w = "; ".join(f"{x['ru']} {x['min']} мин" + (f" {x['km']} км" if x['km'] else "") + (f" пульс {x['hr']}" if x['hr'] else "") for x in d["workouts"]) or "—"
        lines.append(f"{d['date'][5:]} {d['dow']}  {sl}  {d['rhr'] or '—':>3}  {(str(d['hrv']) + '(' + str(d['hrvN']) + ')') if d['hrv'] else '—':7} {d['steps'] if d['steps'] is not None else '—':>6} {d['kcal'] if d['kcal'] is not None else '—':>5}  {w}"
                     + ("   ← без часов" if d["noWatch"] else ""))
    lines.append("")
    long = daily(db, 28, now.date())
    for label, tbl in (("7 дней", table), ("28 дней", long)):
        # Days without the watch are excluded from everything the watch measures, otherwise a week of not wearing
        # it reads as a collapse in sleep and calories. Steps come from the phone, so they count every day.
        worn = [d for d in tbl if not d["noWatch"]]
        skipped = len(tbl) - len(worn)
        lines.append(f"Средние за {label}: сон {hm(avg([d['sleep']['total'] for d in worn if d['sleep']]))}, глубокий {hm(avg([d['sleep']['deep'] for d in worn if d['sleep']]))}, "
                     f"RHR {avg([d['rhr'] for d in worn]) or '—'}, HRV {avg([d['hrv'] for d in worn]) or '—'}, шаги {avg([d['steps'] for d in tbl]) or '—'}, "
                     f"ккал {avg([d['kcal'] for d in worn]) or '—'}, тренировок {sum(len(d['workouts']) for d in tbl)}"
                     + (f" (дней без часов: {skipped}, они в средние по сну/HRV/RHR/ккал не вошли)" if skipped else ""))
    lines.append("")
    prev = db.execute("SELECT ts, text FROM reviews ORDER BY id DESC LIMIT 3").fetchall()
    if prev:
        lines.append("Предыдущие заметки (не повторяй их дословно, если ничего не изменилось — просто подтверди коротко):")
        for r in prev:
            lines.append(f"- [{fmt_local(r['ts'])}] {r['text'].strip()}")
    return "\n".join(lines)


def pending(db=None):
    db = db or connect()
    li, lr = last_ingest(db), last_review(db)
    new = db.execute("SELECT COUNT(*) c FROM samples WHERE ingested_at > ?", (lr["samples_until"] if lr and lr["samples_until"] else "",)).fetchone()["c"]
    return {"lastIngest": li, "lastReview": lr["ts"] if lr else None, "newSamples": new, "ok": bool(li) and new > 0}


def review_save(text, model=None, digest_text=None, db=None):
    db = db or connect()
    with db:
        db.execute("INSERT INTO reviews(ts, model, text, digest, samples_until) VALUES(?,?,?,?,?)",
                   (iso(utcnow()), model, text.strip(), digest_text, last_ingest(db)))
    return last_review(db)


def summary(db=None, days=14):
    db = db or connect()
    lr = last_review(db)
    tbl = daily(db, days)
    # A note written during the night ("ложись спать") is worse than no note at all over breakfast, so anything
    # older than this morning is marked stale and the pages stop showing it as current advice.
    stale = bool(lr) and parse_ts(lr["ts"]) < daybreak().astimezone(dt.timezone.utc)
    return {"lastIngest": last_ingest(db), "tz": tz().key,
            "review": {"ts": lr["ts"], "model": lr["model"], "text": lr["text"], "stale": stale} if lr else None,
            "reviews": [{"ts": r["ts"], "model": r["model"], "text": r["text"]} for r in db.execute("SELECT ts, model, text FROM reviews ORDER BY id DESC LIMIT 30")],
            "days": tbl,
            "avg7": {"sleep": avg([d["sleep"]["total"] for d in tbl[:7] if d["sleep"]]), "rhr": avg([d["rhr"] for d in tbl[:7]]),
                     "hrv": avg([d["hrv"] for d in tbl[:7]]), "steps": avg([d["steps"] for d in tbl[:7]])}}


# ---------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("digest"); p.add_argument("--days", type=int, default=7)
    sub.add_parser("pending")
    p = sub.add_parser("review-save"); p.add_argument("--model"); p.add_argument("--file", required=True); p.add_argument("--digest-file")
    p = sub.add_parser("reviews"); p.add_argument("--limit", type=int, default=10)
    sub.add_parser("summary")
    p = sub.add_parser("ingest"); p.add_argument("type", choices=["workouts", "sleep", "metrics"]); p.add_argument("file")
    sub.add_parser("stats")
    p = sub.add_parser("backup"); p.add_argument("--keep", type=int, default=7)
    a = ap.parse_args(argv)
    db = connect()
    if a.cmd == "digest":
        print(digest(db, a.days))
    elif a.cmd == "pending":
        st = pending(db)
        print(json.dumps(st, ensure_ascii=False))
        sys.exit(0 if st["ok"] else 1)
    elif a.cmd == "review-save":
        text = open(a.file, encoding="utf-8").read()
        if not text.strip():
            sys.exit("empty review, not saved")
        dg = open(a.digest_file, encoding="utf-8").read() if a.digest_file else None
        r = review_save(text, a.model, dg, db)
        print(f"saved review #{r['id']} at {r['ts']}")
    elif a.cmd == "reviews":
        for r in db.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (a.limit,)):
            print(f"[{fmt_local(r['ts'])}] ({r['model']})\n{r['text']}\n")
    elif a.cmd == "summary":
        print(json.dumps(summary(db), ensure_ascii=False, indent=1))
    elif a.cmd == "ingest":
        print(json.dumps(ingest(a.type, json.load(open(a.file, encoding="utf-8")))))
    elif a.cmd == "stats":
        for r in db.execute("SELECT type, kind, COUNT(*) n, MIN(start) first, MAX(\"end\") last FROM samples WHERE deleted_at IS NULL GROUP BY type, kind ORDER BY type, kind"):
            print(f"{r['type']:8} {r['kind']:22} {r['n']:7}  {r['first']} … {r['last']}")
        lr = last_review(db)
        print(f"reviews: {db.execute('SELECT COUNT(*) c FROM reviews').fetchone()['c']}, last at {lr['ts'] if lr else '—'}")
    elif a.cmd == "backup":
        r = backup(a.keep, db)
        print(f"{r['file']}  {r['rows']} rows, {r['bytes'] // 1024} KB, {r['kept']} kept"
              + (f", dropped {', '.join(r['dropped'])}" if r["dropped"] else ""))


if __name__ == "__main__":
    main()
