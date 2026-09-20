#!/usr/bin/env python3
"""Ночь против чего угодно (20.09.2026).

У сна есть стадии, у вечера — обстоятельства, и сайт единственный, кто знает и то и другое: тренировка,
кончившаяся после семи (health.db), занятие или встреча вечером (календарь), поездка на такси в этот
день (чеки), градусы в спальне (датчик). Здесь ночь за ночью раскладывается по этим признакам, и для
каждого считается медиана сна, глубокого сна и отбоя «с ним» против «без него». Арифметика, без модели:
цифры можно перепроверить глазами, а «после французского ложишься позже» — это либо видно в медианах,
либо нет.

Сравнение молчит, пока ночей меньше MIN_NIGHTS или на одной из сторон меньше MIN_SIDE: на пяти ночах это
было бы не наблюдение, а совпадение. Итог уходит в итоги недели (scripts/week.py), когда данных хватает.

  python3 scripts/nights.py [--days 60]        # таблица ночей и сравнения
  python3 scripts/nights.py --json
"""
import argparse
import datetime as dt
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health as H  # noqa: E402
import schedule as S  # noqa: E402

MIN_NIGHTS = 14
MIN_SIDE = 5
EVENING = 19               # тренировка или событие, кончившиеся после этого часа, — вечерние
TAXI = "Яндекс.Такси"
ROUTINE = ("работа",)      # рабочий блок стоит каждый будний день и вечером не является
FACTORS = [("workout", "вечерняя тренировка"), ("event", "вечернее занятие или встреча"), ("taxi", "такси в этот день")]


def hm(minutes):
    m = int(round(minutes or 0))
    return f"{m // 60}ч{m % 60:02d}"


def _bed(n):
    """Отбой в минутах после 18:00 вечера накануне — так полночь не рвёт медиану пополам."""
    base = dt.datetime.combine(n["eve"], dt.time(18, 0), n["bed"].tzinfo)
    return (n["bed"] - base).total_seconds() / 60


def _bed_text(mins):
    t = dt.datetime.combine(dt.date.today(), dt.time(18, 0)) + dt.timedelta(minutes=mins)
    return t.strftime("%H:%M")


def nights(db=None, days=60):
    db = db or H.connect()
    since = H.utcnow() - dt.timedelta(days=days)
    out = []
    for n in H.nights(H.rows(db, since, type_="sleep")):
        if not n.get("total"):
            continue
        end, start = H.local(n["end"]), H.local(n["start"])
        if end.hour >= H.SLEEP_EVENING:      # кусок, кончившийся вечером, — не ночь
            continue
        out.append({"date": end.date().isoformat(), "eve": end.date() - dt.timedelta(days=1),
                    "total": n["total"], "deep": n.get("deep") or 0, "bed": start,
                    "start": n["start"], "end": n["end"],
                    "workout": False, "event": False, "taxi": False, "temp": None})
    out.sort(key=lambda n: n["date"])
    if not out:
        return out
    first = out[0]["eve"]
    # Вечерняя тренировка — кончилась после EVENING в вечер накануне ночи.
    late = set()
    for w in H.rows(db, since - dt.timedelta(days=1), type_="workout"):
        e = H.local(w["end"])
        if e.hour >= EVENING:
            late.add(e.date())
    # Вечернее событие календаря — занятое, не на весь день, кончается после EVENING; рабочий блок не в счёт.
    evening = set()
    try:
        ag = S.agenda(days=(dt.date.today() - first).days + 1, start_date=first, skip=S.hidden_calendars())
        for day, evs in ag.items():
            for e in evs:
                if e["busy"] and not e["allday"] and e["end"].hour >= EVENING \
                        and (e["summary"] or "").strip().lower() not in ROUTINE:
                    evening.add(day)
    except Exception as e:
        print(f"nights: календарь не прочитался: {type(e).__name__}: {e}", file=sys.stderr)
    # Такси — по чекам из lkdr; их нет на машине без данных, и это не ошибка.
    taxi = set()
    try:
        import spend as SP
        for r in SP.rows(since=first.isoformat()):
            if r["merchant"] == TAXI:
                taxi.add(dt.date.fromisoformat(r["date"]))
    except Exception:
        pass
    climate = None
    try:
        import sensors as SN
        climate = lambda n: (SN.night_climate(db, n["start"], n["end"]) or {}).get("temp")  # noqa: E731
    except Exception:
        pass
    for n in out:
        n["workout"] = n["eve"] in late
        n["event"] = n["eve"] in evening
        n["taxi"] = n["eve"] in taxi
        if climate:
            try:
                n["temp"] = climate(n)
            except Exception:
                n["temp"] = None
    return out


def compare(ns, key, label):
    with_, without = [n for n in ns if n[key]], [n for n in ns if not n[key]]
    if len(with_) < MIN_SIDE or len(without) < MIN_SIDE:
        return None
    med = lambda xs, f: st.median(f(x) for x in xs)  # noqa: E731
    return {"factor": key, "label": label, "n": len(with_), "m": len(without),
            "sleep": (med(with_, lambda n: n["total"]), med(without, lambda n: n["total"])),
            "deep": (med(with_, lambda n: n["deep"]), med(without, lambda n: n["deep"])),
            "bed": (med(with_, _bed), med(without, _bed))}


def compare_temp(ns):
    """Тёплые ночи против прохладных — две половины по медиане, как в sensors.py."""
    have = [n for n in ns if n.get("temp") is not None]
    if len(have) < MIN_NIGHTS:
        return None
    mid = st.median(n["temp"] for n in have)
    warm, cool = [n for n in have if n["temp"] > mid], [n for n in have if n["temp"] <= mid]
    if len(warm) < MIN_SIDE or len(cool) < MIN_SIDE:
        return None
    med = lambda xs, f: st.median(f(x) for x in xs)  # noqa: E731
    return {"factor": "temp", "label": f"в спальне теплее {mid:.1f}°", "n": len(warm), "m": len(cool),
            "sleep": (med(warm, lambda n: n["total"]), med(cool, lambda n: n["total"])),
            "deep": (med(warm, lambda n: n["deep"]), med(cool, lambda n: n["deep"])),
            "bed": (med(warm, _bed), med(cool, _bed))}


def findings(ns):
    if len(ns) < MIN_NIGHTS:
        return []
    out = [c for c in (compare(ns, k, l) for k, l in FACTORS) if c]
    t = compare_temp(ns)
    if t:
        out.append(t)
    return out


def fmt_finding(c):
    return (f"{c['label']} ({c['n']} ночей против {c['m']}): сон {hm(c['sleep'][0])} против {hm(c['sleep'][1])}, "
            f"глубокий {hm(c['deep'][0])} против {hm(c['deep'][1])}, отбой {_bed_text(c['bed'][0])} против {_bed_text(c['bed'][1])}")


def report(days=60, db=None):
    ns = nights(db, days)
    lines = [f"Ночи за {days} дн.: {len(ns)}" + (f" — сравнивать рано, нужно {MIN_NIGHTS}" if len(ns) < MIN_NIGHTS else "")]
    lines.append("дата        сон    глубокий  отбой  трен.  событие  такси  спальня")
    for n in ns[::-1]:
        lines.append(f"{n['date']}  {hm(n['total']):6} {hm(n['deep']):8}  {n['bed']:%H:%M}  "
                     f"{'да' if n['workout'] else '—':5}  {'да' if n['event'] else '—':7}  {'да' if n['taxi'] else '—':5}  "
                     f"{(str(n['temp']) + '°') if n.get('temp') is not None else '—'}")
    fs = findings(ns)
    if fs:
        lines.append("")
        lines.append("Сравнения (медианы):")
        lines += [f"- {fmt_finding(c)}" for c in fs]
    elif len(ns) >= MIN_NIGHTS:
        lines.append("")
        lines.append(f"Ни у одного признака нет {MIN_SIDE} ночей на каждой стороне — сравнивать пока нечего.")
    return "\n".join(lines)


def brief(days=60, db=None):
    """Только выводы, для итогов недели; пусто, пока данных мало."""
    fs = findings(nights(db, days))
    return "\n".join(f"- {fmt_finding(c)}" for c in fs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.json:
        ns = nights(days=a.days)
        for n in ns:
            n["eve"] = n["eve"].isoformat(); n["bed"] = n["bed"].isoformat()
        print(json.dumps({"nights": ns, "findings": findings(ns)}, ensure_ascii=False, indent=1, default=str))
    else:
        print(report(a.days))


if __name__ == "__main__":
    main()
