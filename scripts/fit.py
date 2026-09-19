#!/usr/bin/env python3
"""Чем занять дыру в дне: что влезает в ближайшее свободное окно.

Сайт — единственное место, где сходятся календарь, погода, закат и длина каждой лекции, микса и статьи
вместе с тем, где я в них остановился. Отсюда и вопрос, который больше никто задать не может: не «что
посмотреть вообще», а «до 23:00 четыре часа, дождя нет, светло ещё час — что из этого влезет целиком».

Советы не выдумываются: берётся то, что уже начато или уже лежит в планах, и отбирается по длине.
Прогулка — отдельный случай: её решают закат и осадки внутри самого окна, а не «погода на сегодня».

  python3 scripts/fit.py            # то, что видит карточка на главной (JSON)
  python3 scripts/fit.py text       # то же словами
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health as H  # noqa: E402
import lectures as L  # noqa: E402
import mixes as X  # noqa: E402
import reads as RD  # noqa: E402
import french as FR  # noqa: E402
import schedule as S  # noqa: E402
import sun as SUN  # noqa: E402
import weather as W  # noqa: E402

WALK_MIN = 40          # окно короче — уже не прогулка
WALK_MAX = 70          # больше часа с небольшим советовать самонадеянно
WALK_LIGHT = 25        # светлого времени внутри окна должно быть хотя бы столько
WET = 50               # вероятность осадков (%), при которой гулять уже не советуют
COLD = -12             # и мороз, при котором тоже
STEPS_ENOUGH = 8000    # столько шагов за день — прогулку можно и не предлагать
ARTICLE_MIN = 12       # у большинства лент длины нет; столько читается средняя статья
LESSON_EXTRA = 8       # разбор и тест поверх самого материала
PICKS = 3


def hm(minutes):
    m = int(round(minutes or 0))
    return f"{m // 60} ч {m % 60:02d} мин" if m >= 60 else f"{m} мин"


def at(hhmm, date, zone):
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(date, dt.time(h, m), zone)


def steps_today(now):
    """Шаги с начала дня — одним запросом, без полного дайджеста: карточка рисуется на каждом заходе."""
    try:
        db = H.connect()
        start = dt.datetime.combine(now.date(), dt.time(0, 0), now.tzinfo)
        rows = [r for r in H.rows(db, start.astimezone(dt.timezone.utc), type_="metric", kind="steps")]
        return round(H.lane_sum(rows)) or None
    except Exception:
        return None


def weather_in(start, end, zone):
    """Погода внутри окна, а не «на сегодня»: дождь в семь утра прогулке в восемь вечера не помеха."""
    try:
        wx = W.today()
    except Exception:
        return None
    hours = []
    for h in wx.get("hours") or []:
        if not h.get("day"):
            continue
        t = at(h["time"], start.date(), zone)
        if start - dt.timedelta(minutes=59) <= t < end:
            hours.append(h)
    if not hours:
        return {"temp": wx["now"]["temp"], "precip": wx["today"].get("precipProbMax") or 0, "icon": wx["now"]["icon"]}
    return {"temp": min(h["temp"] for h in hours), "precip": max(h.get("precipProb") or 0 for h in hours),
            "icon": hours[0].get("icon") or wx["now"]["icon"]}


def walk_pick(start, end, minutes, wx, steps, zone):
    """Прогулка, если в окне есть светлое время, нет дождя и день пока малоподвижный."""
    if minutes < WALK_MIN:
        return None
    set_ = SUN.sunset(start.date(), zone=zone)
    light_until = min(end, set_) if set_ else end
    light = (light_until - start).total_seconds() / 60
    if light < WALK_LIGHT:
        return None
    if wx and (wx["precip"] >= WET or wx["temp"] <= COLD):
        return None
    if steps and steps >= STEPS_ENOUGH:
        return None
    mins = int(min(WALK_MAX, minutes, max(WALK_LIGHT, light)))
    bits = [f"светло до {light_until:%H:%M}"]
    if wx:
        bits.append(f"{wx['temp']}°")
        bits.append("без осадков" if wx["precip"] < 20 else f"осадки {wx['precip']}%")
    if steps:
        bits.append(f"сегодня {steps:,} шагов".replace(",", " "))
    why = ", ".join(bits)
    return {"kind": "walk", "title": "Прогулка", "sub": hm(mins), "minutes": mins, "fits": True,
            "url": None, "image": None, "why": why}


def best(cands, minutes):
    """Лучший кандидат под окно: сперва начатое из того, что влезает целиком, потом самое длинное из
    влезающего — дыра в четыре часа просит лекцию, а не пятиминутку. Если не влезает ничего, берётся
    самое короткое: пусть карточка честно скажет «не влезет», чем промолчит."""
    cands = [c for c in cands if c and c.get("minutes")]
    if not cands:
        return None
    fits = [c for c in cands if c["minutes"] <= minutes]
    if fits:
        started = [c for c in fits if c.get("resume")]
        return max(started or fits, key=lambda c: c["minutes"])
    return min(cands, key=lambda c: c["minutes"])


def lecture_pick(now, minutes):
    db = L.load()
    ls = db.get("lectures") or []
    listening = sorted([l for l in ls if l.get("status") == "listening"], key=lambda l: -(l.get("touchedAt") or 0))
    queued = sorted([l for l in ls if l.get("status") == "queued"], key=lambda l: (l.get("queuedAt") or 0))
    cands = []
    for l in listening + queued:
        if not l.get("duration"):
            continue
        resume = l.get("status") == "listening"
        left = (l["duration"] - (l.get("position") or 0)) / 60
        if left < 2:
            continue
        ch = next((c for c in db.get("channels") or [] if c["id"] == l.get("channel")), {})
        cands.append({"kind": "lecture", "title": l["title"], "minutes": round(left), "resume": resume,
                      "sub": (f"осталось {hm(left)}" if resume else hm(left)) + (f" · {ch.get('label') or ch.get('name')}" if ch else ""),
                      "url": "lectures.html#" + l["id"],
                      "image": l.get("artwork") or (None if l.get("source") == "soundcloud" else f"https://i.ytimg.com/vi/{l['id']}/mqdefault.jpg"),
                      "why": "начатая лекция" if resume else "в планах"})
    return best(cands, minutes)


def mix_pick(now, minutes):
    mixes = X.load()
    morning = now.hour < 9
    pool = [m for m in mixes if "calm" in (m.get("tags") or [])] if morning else list(mixes)
    pool = pool or list(mixes)
    cands = []
    for m in pool:
        if not m.get("duration"):
            continue
        pos = m.get("position") or 0
        resume = bool(pos) and pos < m["duration"] - 120
        left = (m["duration"] - pos) / 60 if resume else m["duration"] / 60
        if left < 2:
            continue
        cands.append({"kind": "mix", "title": m["title"], "minutes": round(left), "resume": resume,
                      "sub": (f"осталось {hm(left)}" if resume else hm(left)) + (f" · {m.get('artist')}" if m.get("artist") else ""),
                      "url": "mixes.html#" + m["id"], "image": m.get("artwork"),
                      "why": "недослушанный микс" if resume else ("спокойное под утро" if morning else "из коллекции")})
    return best(cands, minutes)


def article_pick(now, minutes):
    db = RD.load()
    cands = []
    for a, saved in [(a, True) for a in db.get("saved") or []] + [(a, False) for a in db.get("items") or []]:
        mins = a.get("minutes") or ARTICLE_MIN
        cands.append({"kind": "article", "title": a["title"], "minutes": mins, "resume": saved,
                      "sub": f"{hm(mins)} · {a.get('source') or ''}".strip(" ·"),
                      "url": a["url"], "external": True, "image": a.get("image"),
                      "why": "отложено в список чтения" if saved else "совет дня"})
    return best(cands, minutes)


def french_pick(now, minutes):
    db = FR.load()
    cands = []
    for m in db.get("items") or []:
        if not m.get("lesson"):
            continue
        mins = (m.get("minutes") or ARTICLE_MIN) + LESSON_EXTRA
        # Урок и перевод — не «материал с разбором», а своё занятие, и подпись должна это говорить.
        what = {"lesson": "урок с заданиями", "translation": "перевод фраз"}.get(m.get("kind"))
        cands.append({"kind": "french", "title": m["title"], "minutes": mins, "resume": False,
                      "sub": f"{hm(mins)} · {what}" if what
                             else f"{hm(mins)} с разбором · {m.get('source') or ''}".strip(" ·"),
                      "url": "french.html", "image": m.get("image"),
                      "why": "занятие на сегодня" if what else "материал на сегодня"})
    return best(cands, minutes)


def plan(now=None):
    zone = SUN.tz()
    now = now or dt.datetime.now(zone)
    try:
        day = S.day_plan()
    except Exception as e:
        return {"now": now.isoformat(), "window": None, "picks": [], "error": f"{type(e).__name__}: {e}"}
    free = (day or {}).get("free") or []
    if not free:
        return {"now": now.isoformat(), "window": None, "picks": []}
    w = free[0]
    date = dt.date.fromisoformat(day["date"])
    start, end = at(w["from"], date, zone), at(w["to"], date, zone)
    minutes = w["minutes"]
    wx = weather_in(start, end, zone)
    steps = steps_today(now)
    picks = []
    walk = walk_pick(start, end, minutes, wx, steps, zone)
    if walk:
        picks.append(walk)
    rest = []
    for f in (lecture_pick, mix_pick, article_pick, french_pick):
        try:
            p = f(now, minutes)
        except Exception as e:
            print(f"fit {f.__name__}: {type(e).__name__}: {e}", file=sys.stderr)
            p = None
        if p:
            rest.append(p)
    # Сначала то, что влезает целиком, потом начатое, потом то, что занимает окно плотнее: обрывать
    # лекцию на середине окна — это не совет, а список ссылок.
    def score(p):
        s = 100 if p["minutes"] <= minutes else 0
        s += 30 if p.get("resume") else 0
        s += min(20, 20 * p["minutes"] / max(minutes, 1)) if p["minutes"] <= minutes else 0
        return s
    rest.sort(key=score, reverse=True)
    for p in rest:
        if len(picks) >= PICKS:
            break
        p["fits"] = p["minutes"] <= minutes
        picks.append(p)
    return {"now": now.isoformat(), "date": day["date"],
            "window": {"from": w["from"], "to": w["to"], "minutes": minutes, "length": hm(minutes),
                       "open": start <= now + dt.timedelta(minutes=5)},
            "weather": wx, "steps": steps, "picks": picks}


def text(p=None):
    p = p or plan()
    w = p.get("window")
    if not w:
        return "Свободного окна на сегодня нет."
    head = (f"Свободно {w['from']}–{w['to']} ({hm(w['minutes'])})"
            + (f", {p['weather']['temp']}°, осадки {p['weather']['precip']}%" if p.get("weather") else ""))
    lines = [head]
    for it in p["picks"]:
        lines.append(f"  · {it['title']} — {it['sub']} ({it['why']})" + ("" if it.get("fits", True) else " — не влезет целиком"))
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "text":
        print(text())
    else:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
