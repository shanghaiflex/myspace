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
import argparse, datetime as dt, json, os, sqlite3, sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("HEALTH_DB") or os.path.join(ROOT, "health.db")
BACKUP_DIR = os.environ.get("HEALTH_BACKUP_DIR") or os.path.join(ROOT, "backups")
METRIC_KINDS = ("hrv_sdnn", "resting_heart_rate", "steps", "active_energy")
WORKOUT_RU = {"running": "бег", "cycling": "велосипед", "walking": "ходьба", "swimming": "плавание", "yoga": "йога",
              "functionalStrengthTraining": "силовая", "functional_strength_training": "силовая", "traditionalStrengthTraining": "силовая",
              "hiking": "поход", "elliptical": "эллипс", "rowing": "гребля", "coreTraining": "кор", "other": "другое"}
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
                    row = ("sleep", "sleep", float(it["totalMinutes"]), "min")
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


def day_key(s):
    return local(s).strftime("%Y-%m-%d")


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
    buckets = {}
    for r in rows(db, since):
        key = day_key(r["end"] if r["type"] == "sleep" else r["start"])
        if key in out:
            buckets.setdefault((key, r["type"], r["kind"]), []).append(r)
    for (key, type_, kind), ss in buckets.items():
        d = out[key]
        if type_ == "sleep":
            tot = {"total": 0, "deep": 0, "rem": 0, "core": 0, "awake": 0}
            for s in ss:
                raw = json.loads(s["data"]); b = raw.get("breakdown") or {}
                tot["total"] += s["value"] or 0
                tot["deep"] += b.get("deepMinutes") or 0; tot["rem"] += b.get("remMinutes") or 0
                tot["core"] += b.get("coreMinutes") or 0; tot["awake"] += b.get("awakeMinutes") or 0
            main = max(ss, key=lambda s: s["value"] or 0)
            tot["bed"] = local(main["start"]).strftime("%H:%M"); tot["wake"] = local(main["end"]).strftime("%H:%M")
            tot["sessions"] = len(ss)
            d["sleep"] = {k: (round(v) if isinstance(v, float) else v) for k, v in tot.items()}
        elif type_ == "workout":
            for s in ss:
                raw = json.loads(s["data"])
                d["workouts"].append({"type": kind, "ru": WORKOUT_RU.get(kind, kind), "start": local(s["start"]).strftime("%H:%M"),
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


def digest(db=None, days=7):
    """Human-readable digest for the review model: daily table, averages, workouts, freshness."""
    db = db or connect()
    now = dt.datetime.now(tz())
    li = last_ingest(db)
    n_all = db.execute("SELECT COUNT(*) c FROM samples WHERE deleted_at IS NULL").fetchone()["c"]
    lines = [f"Сейчас: {now.strftime('%Y-%m-%d')} ({DOW[now.weekday()]}) {now.strftime('%H:%M')} {tz().key}",
             f"Последние данные с телефона: {fmt_local(li)}" + (f" ({int((utcnow() - parse_ts(li)).total_seconds() // 60)} мин назад)" if li else "") + f", всего образцов в базе: {n_all}", ""]
    table = daily(db, days, now.date())
    lines.append("Дни (сон засчитан на утро пробуждения; шаги/ккал приблизительно, если часы и телефон считали одновременно):")
    lines.append("дата       сон    глуб/REM    отбой–подъём  RHR  HRV(n)   шаги   ккал  тренировки")
    for d in table:
        s = d["sleep"]
        sl = f"{hm(s['total']):6} {hm(s['deep'])}/{hm(s['rem'])}  {s['bed']}–{s['wake']}" if s else f"{'—':6} {'—':10} {'—':12}"
        w = "; ".join(f"{x['ru']} {x['min']} мин" + (f" {x['km']} км" if x['km'] else "") + (f" пульс {x['hr']}" if x['hr'] else "") for x in d["workouts"]) or "—"
        lines.append(f"{d['date'][5:]} {d['dow']}  {sl}  {d['rhr'] or '—':>3}  {(str(d['hrv']) + '(' + str(d['hrvN']) + ')') if d['hrv'] else '—':7} {d['steps'] if d['steps'] is not None else '—':>6} {d['kcal'] if d['kcal'] is not None else '—':>5}  {w}")
    lines.append("")
    long = daily(db, 28, now.date())
    for label, tbl in (("7 дней", table), ("28 дней", long)):
        lines.append(f"Средние за {label}: сон {hm(avg([d['sleep']['total'] for d in tbl if d['sleep']]))}, глубокий {hm(avg([d['sleep']['deep'] for d in tbl if d['sleep']]))}, "
                     f"RHR {avg([d['rhr'] for d in tbl]) or '—'}, HRV {avg([d['hrv'] for d in tbl]) or '—'}, шаги {avg([d['steps'] for d in tbl]) or '—'}, "
                     f"ккал {avg([d['kcal'] for d in tbl]) or '—'}, тренировок {sum(len(d['workouts']) for d in tbl)}")
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
    return {"lastIngest": last_ingest(db), "tz": tz().key,
            "review": {"ts": lr["ts"], "model": lr["model"], "text": lr["text"]} if lr else None,
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
