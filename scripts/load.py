#!/usr/bin/env python3
"""Нагрузка тренировок за несколько дней и старты впереди — то, чего часовая заметка о здоровье не видела.

Заметка смотрела на день и неделю по отдельности и хвалила «так держать» за 16 тысяч шагов, пока HRV была 52
при обычных 68, а подряд шли забег на 10 км, три часа тенниса и назавтра 50 км на велосипеде (26.09.2026).
Вдобавок она не знала, что пробежка — это забег, а теннис, который часы не записали, для неё не существовал.

Отсюда две вещи:

  Нагрузка — сумма активных ккал за тренировки (ходьба не считается: это движение дня, его видно по шагам).
  Ккал выбраны не случайно: Apple считает их по пульсу, так что интенсивность внутри уже есть, и цифру можно
  проверить глазами в «Здоровье». Сравнивается неделя, кончая сегодня, с обычной неделей четырёх предыдущих
  (ACUTE_DAYS против CHRONIC_WEEKS) — отношение выше 1,3 значит «больше привычного», выше 1,5 — резкий скачок.

  Календарь как источник тренировок. Событие со спортом в названии (SPORTS) сопоставляется с записью часов
  по времени: совпало — тренировка получает имя события («забег: Московский марафон»), не совпало — считается
  по календарю с оценкой ккал по моему же расходу в минуту для этого вида, но не больше моей обычной
  такой тренировки (блок в календаре часто шире самой игры). Старт (RACE) впереди на RACE_AHEAD дней
  выводится отдельно, с погодой на месте старта, если место удаётся найти в названии.

Usage:
  load.py digest            блок «Нагрузка» для заметки (его подставляет health_review.sh)
  load.py json              то же данными
"""
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health as H  # noqa: E402

ACUTE_DAYS = 7
CHRONIC_WEEKS = 4
HISTORY_DAYS = 180          # откуда берётся мой расход в минуту по видам
SKIP = {"walking"}          # движение дня, а не тренировка
MATCH_SLACK = 60            # минут: запись часов и событие календаря — одно и то же, если так близко
RACE_AHEAD = 3              # дней вперёд, на которые смотрим старты
FALLBACK_RATE, FALLBACK_CAP = 7.0, 1000   # ккал/мин и потолок для вида, которого часы ещё не видели

# Вид спорта по словам в названии события. Порядок важен: «велозабег» — велосипед, а не бег.
SPORTS = [
    ("cycling", r"вело|gran ?fondo|гранфондо|шоссейн|\bbike\b|cycling"),
    ("swimBikeRun", r"триатлон|triathlon|ironman|айронмен"),
    ("swimming", r"плаван|заплыв|бассейн|swim"),
    ("running", r"марафон|забег|пробежк|\bбег\b|трейл|parkrun|паркран|\brun\b"),
    ("tennis", r"теннис|tennis|падел|padel"),
    ("traditionalStrengthTraining", r"силов|тренировка в зале|\bзал\b|gym"),
]
RACE = r"марафон|забег|gran ?fondo|гранфондо|гонк|триатлон|ironman|айронмен|заплыв|трейл|старт|\b\d+\s*км\b"
# Слова, которые не место: для поиска старта на карте.
NOT_PLACE = r"марафон|полумарафон|забег|gran|fondo|гранфондо|гонка|триатлон|заплыв|трейл|старт|км|московский"


def kind_of(title):
    t = (title or "").lower()
    return next((k for k, pat in SPORTS if re.search(pat, t)), None)


def is_race(title):
    return bool(re.search(RACE, (title or "").lower()))


def workouts(db, since):
    out = []
    for r in H.rows(db, since, "workout"):
        k = H.workout_kind(r["kind"])
        raw = json.loads(r["data"] or "{}")
        out.append({"kind": k, "start": H.parse_ts(r["start"]).astimezone(H.tz()), "end": H.parse_ts(r["end"]).astimezone(H.tz()),
                    "min": round(r["value"] or 0), "kcal": round(raw["calories"]) if raw.get("calories") else None,
                    "km": round((raw.get("distanceMeters") or 0) / 1000, 2) or None,
                    "hr": round(raw["averageHeartRate"]) if raw.get("averageHeartRate") else None})
    return out


def rates(ws):
    """Мой расход по видам: медиана ккал в минуту и обычная (медианная) тренировка целиком — для оценки того, что
    часы не записали. Потолок именно обычная, а не самая тяжёлая: блок в календаре шире игры, а от оценки
    зависит, увидит ли заметка скачок нагрузки, — пусть лучше недооценит, чем напугает."""
    by = {}
    for w in ws:
        if w["kcal"] and w["min"] >= 10:
            by.setdefault(w["kind"], []).append(w)
    return {k: (statistics.median(x["kcal"] / x["min"] for x in v), statistics.median(x["kcal"] for x in v)) for k, v in by.items()}


def calendar(first, days):
    """Мои события со спортом в названии, от `first` на `days` дней. Без сети и без календаря — пусто, а не ошибка."""
    try:
        import schedule as S
        cal = S.agenda(days=days, start_date=first, skip=S.hidden_calendars())
    except Exception:
        return []
    out = []
    for date, evs in cal.items():
        for e in evs:
            k = kind_of(e["summary"])
            if k and not e.get("who"):          # «теннис Полины» — не моя нагрузка
                out.append({"title": e["summary"], "kind": k, "start": e["start"], "end": e["end"],
                            "allday": e["allday"], "date": date, "race": is_race(e["summary"])})
    return sorted(out, key=lambda e: e["start"])


def overlaps(w, e):
    """Та же тренировка: тот же вид (или часы не знали вида) и близко по времени. Без вида пробежка в 09:51
    сопоставлялась с теннисом в 11:00 — он в пределах часа, — а сам теннис из нагрузки пропадал."""
    if w["kind"] != e["kind"] and w["kind"] not in ("other", "crossTraining", "mixedCardio"):
        return False
    slack = dt.timedelta(minutes=MATCH_SLACK)
    if e["allday"]:
        return w["start"].date() == e["date"]
    return w["start"] < e["end"] + slack and w["end"] > e["start"] - slack


def sessions(db, now=None):
    """Все тренировки за окно — записанные часами и те, что есть только в календаре."""
    now = now or dt.datetime.now(H.tz())
    today = now.date()
    first = today - dt.timedelta(days=ACUTE_DAYS * (CHRONIC_WEEKS + 1) - 1)
    ws = workouts(db, dt.datetime.combine(today - dt.timedelta(days=HISTORY_DAYS), dt.time(0), H.tz()))
    rate = rates(ws)
    ws = [w for w in ws if w["start"].date() >= first and w["kind"] not in SKIP]
    evs = calendar(first, (today - first).days + 1 + RACE_AHEAD)
    past, ahead = [], []
    for e in evs:
        if e["start"] > now:
            ahead.append(e)
            continue
        hit = [w for w in ws if not w.get("event") and overlaps(w, e)]
        if hit:
            for w in hit:
                w["event"] = e["title"]; w["race"] = e["race"]
            continue
        if e["allday"] or e["date"] > today:
            continue            # без часов длительности у события на весь день нет — считать нечего
        minutes = round((min(e["end"], now) - e["start"]).total_seconds() / 60)
        if minutes < 10:
            continue            # только началось
        r, cap = rate.get(e["kind"], (FALLBACK_RATE, FALLBACK_CAP))
        past.append({"kind": e["kind"], "start": e["start"], "end": e["end"], "min": minutes, "kcal": round(min(r * minutes, cap)),
                     "km": None, "hr": None, "event": e["title"], "race": e["race"], "calendar": True})
    return sorted(ws + past, key=lambda w: w["start"]), ahead, rate


def summary(db=None, now=None):
    db = db or H.connect()
    now = now or dt.datetime.now(H.tz())
    today = now.date()
    ss, ahead, _ = sessions(db, now)
    acute_from = today - dt.timedelta(days=ACUTE_DAYS - 1)
    chronic_from = acute_from - dt.timedelta(days=ACUTE_DAYS * CHRONIC_WEEKS)
    acute = [s for s in ss if s["start"].date() >= acute_from]
    chronic = [s for s in ss if chronic_from <= s["start"].date() < acute_from]
    a_kcal = sum(s["kcal"] or 0 for s in acute)
    c_week = sum(s["kcal"] or 0 for s in chronic) / CHRONIC_WEEKS
    ratio = round(a_kcal / c_week, 2) if c_week else None
    trained = {s["start"].date() for s in ss}
    rest = next((today - dt.timedelta(days=i) for i in range(1, ACUTE_DAYS * CHRONIC_WEEKS) if today - dt.timedelta(days=i) not in trained), None)
    return {"now": now, "acute": {"kcal": a_kcal, "n": len(acute), "min": sum(s["min"] for s in acute)},
            "chronicWeek": {"kcal": round(c_week), "n": round(len(chronic) / CHRONIC_WEEKS, 1),
                            "min": round(sum(s["min"] for s in chronic) / CHRONIC_WEEKS)},
            "ratio": ratio, "lastRest": rest, "recent": [s for s in ss if s["start"].date() >= today - dt.timedelta(days=2)],
            "ahead": ahead}


def verdict(ratio):
    if ratio is None:
        return "сравнить не с чем"
    if ratio > 1.5:
        return "резкий скачок против привычного: в такие недели чаще всего и случаются травмы и перетренированность"
    if ratio > 1.3:
        return "больше привычного"
    if ratio < 0.8:
        return "меньше привычного"
    return "в привычных пределах"


# ---------------------------------------------------------------- погода на старте
def geocode(title):
    """Место старта из названия («Gran Fondo Руза, 50 км» → Руза). Если в названии места нет — None."""
    words = [w for w in re.findall(r"[A-ZА-ЯЁ][\w-]{2,}", title) if not re.fullmatch(NOT_PLACE, w.lower())]
    for w in words:
        try:
            q = urllib.parse.urlencode({"name": w, "count": 1, "language": "ru", "countryCode": "RU"})
            with urllib.request.urlopen("https://geocoding-api.open-meteo.com/v1/search?" + q, timeout=6) as r:
                hit = (json.load(r).get("results") or [None])[0]
        except Exception:
            return None
        if hit:
            return {"name": hit.get("name") or w, "lat": hit["latitude"], "lon": hit["longitude"]}
    return None


def race_weather(e):
    """Погода в часы старта: где — по названию, иначе дома (WEATHER_LAT/LON)."""
    place = geocode(e["title"]) or {"name": os.environ.get("WEATHER_CITY") or "Москва",
                                    "lat": float(os.environ.get("WEATHER_LAT", "55.7558")),
                                    "lon": float(os.environ.get("WEATHER_LON", "37.6173"))}
    q = urllib.parse.urlencode({"latitude": place["lat"], "longitude": place["lon"], "timezone": H.tz().key,
                                "start_date": e["date"].isoformat(), "end_date": e["date"].isoformat(),
                                "hourly": "temperature_2m,apparent_temperature,precipitation_probability,wind_speed_10m"})
    try:
        with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + q, timeout=8) as r:
            h = json.load(r)["hourly"]
    except Exception:
        return None
    lo, hi = (7, 12) if e["allday"] else (e["start"].hour, max(e["start"].hour, (e["end"] - dt.timedelta(minutes=1)).hour))
    idx = [i for i, t in enumerate(h["time"]) if lo <= int(t[11:13]) <= hi]
    if not idx:
        return None
    pick = lambda k: [h[k][i] for i in idx if h[k][i] is not None]
    t, feels, rain, wind = pick("temperature_2m"), pick("apparent_temperature"), pick("precipitation_probability"), pick("wind_speed_10m")
    if not t:
        return None
    return {"place": place["name"], "from": f"{lo:02d}:00", "to": f"{hi + 1:02d}:00", "tmin": round(min(t)), "tmax": round(max(t)),
            "feels": round(min(feels)) if feels else None, "rain": max(rain) if rain else None,
            "wind": round(max(wind) / 3.6, 1) if wind else None}


# ---------------------------------------------------------------- текст
def fmt_session(s):
    name = H.WORKOUT_RU.get(s["kind"], s["kind"])
    bits = [f"{s['start']:%H:%M} {name} {s['min']} мин"]
    if s["km"]:
        bits.append(f"{s['km']} км")
    if s["hr"]:
        bits.append(f"пульс {s['hr']}")
    bits.append(f"≈{s['kcal']} ккал (оценка)" if s.get("calendar") else (f"{s['kcal']} ккал" if s["kcal"] else "ккал нет"))
    line = " ".join(bits)
    if s.get("event"):
        line = (("СТАРТ «" if s.get("race") else "«") + s["event"] + "»: " + line)
    if s.get("calendar"):
        line += " — есть только в календаре, часы не записали"
    return line


def digest(db=None, weather=True):
    d = summary(db)
    now, today = d["now"], d["now"].date()
    a, c = d["acute"], d["chronicWeek"]
    lines = ["Нагрузка тренировок (активные ккал за тренировки, ходьба не считается):",
             f"- последние {ACUTE_DAYS} дней: {a['kcal']} ккал, {a['n']} тренировок, {H.hm(a['min'])}; "
             f"обычная неделя (среднее четырёх предыдущих): {c['kcal']} ккал, {str(c['n']).replace('.', ',').removesuffix(',0')} тренировок, {H.hm(c['min'])}"]
    if d["ratio"] is not None:
        lines.append(f"- отношение {d['ratio']:.2f}".replace(".", ",") + f" — {verdict(d['ratio'])}")
    if d["lastRest"]:
        ago = (today - d["lastRest"]).days
        lines.append(f"- последний день без тренировок: {d['lastRest']:%d.%m}" + (" (вчера)" if ago == 1 else f" ({ago} дн. назад)"))
    by_day = {}
    for s in d["recent"]:
        by_day.setdefault(s["start"].date(), []).append(s)
    if by_day:
        lines.append("- последние три дня:")
        for day in sorted(by_day, reverse=True):
            label = {0: "сегодня", 1: "вчера"}.get((today - day).days, f"{H.DOW[day.weekday()]} {day:%d.%m}")
            lines.append(f"  {label}: " + "; ".join(fmt_session(s) for s in by_day[day]))
    if d["ahead"]:
        lines.append("Впереди:")
        for e in d["ahead"]:
            day = (e["date"] - today).days
            label = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(day, f"{H.DOW[e['date'].weekday()]} {e['date']:%d.%m}")
            when = "весь день" if e["allday"] else f"{e['start']:%H:%M}–{e['end']:%H:%M}"
            line = f"- {label} {when} {'СТАРТ ' if e['race'] else ''}«{e['title']}» ({H.WORKOUT_RU.get(e['kind'], e['kind'])})"
            if e["race"] and weather:
                w = race_weather(e)
                if w:
                    line += (f"; погода {w['place']} {w['from']}–{w['to']}: {w['tmin']}…{w['tmax']}°"
                             + (f", ощущается {w['feels']}°" if w["feels"] is not None and w["feels"] < w["tmin"] - 1 else "")
                             + (f", осадки до {w['rain']}%" if w["rain"] is not None else "")
                             + (f", ветер до {w['wind']} м/с" if w["wind"] is not None else ""))
            lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "digest"
    if cmd == "digest":
        print(digest())
    elif cmd == "json":
        print(json.dumps(summary(), ensure_ascii=False, indent=1, default=str))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
