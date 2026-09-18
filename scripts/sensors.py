#!/usr/bin/env python3
"""Комната рядом со сном: показания Zigbee-датчиков ложатся в health.db.

Датчики (`home.py`) знают только «сейчас»: их последний отчёт лежит в брокере retained, и никакой
истории у него нет. Поэтому раз в несколько минут launchd снимает показания и кладёт строку в таблицу
`room` — в ту же базу, где сон и пульс, под тот же ночной бэкап. Ключ строки — не время съёмки, а
`last_seen` самого датчика: спящее устройство отчитывается, когда захочет, и один и тот же отчёт мы
видим много раз подряд; `INSERT OR IGNORE` оставляет от него одну строку.

Смысл затеи — `night`: у сна есть стадии, у спальни — градусы, и через месяц можно спросить, падает ли
глубокий сон в тёплые ночи. Раньше этот вопрос нельзя было даже задать: температура нигде не сохранялась.

  python3 scripts/sensors.py log               # снять показания (launchd, раз в 10 минут)
  python3 scripts/sensors.py list [--days 2]   # что записано
  python3 scripts/sensors.py today             # сутки по комнатам — то, что показывает страница «Дом»
  python3 scripts/sensors.py night [--days 30] # ночь за ночью: сон против спальни
"""
import argparse
import datetime as dt
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health as H  # noqa: E402
import home as HM  # noqa: E402

BEDROOM = "bedroom"          # комната, которая интересна рядом со сном

SCHEMA = """
CREATE TABLE IF NOT EXISTS room (
  room TEXT NOT NULL, ts TEXT NOT NULL,
  temperature REAL, humidity REAL, battery REAL,
  PRIMARY KEY (room, ts));
CREATE INDEX IF NOT EXISTS idx_room_ts ON room(ts);
"""


def connect():
    db = H.connect()
    db.executescript(SCHEMA)
    return db


def log(db=None, state=None):
    """Снять текущие показания и записать то, чего ещё нет. Возвращает список записанных комнат."""
    db = db or connect()
    devs = state or HM.state()
    wrote = []
    for room in HM.SENSORS:
        rep = devs.get(room)
        if not rep or rep.get("temperature") is None:
            continue
        try:
            ts = H.iso(H.parse_ts(rep["last_seen"])) if rep.get("last_seen") else H.iso(H.utcnow())
        except Exception:
            ts = H.iso(H.utcnow())
        with db:
            cur = db.execute("INSERT OR IGNORE INTO room(room, ts, temperature, humidity, battery) VALUES(?,?,?,?,?)",
                             (room, ts, rep.get("temperature"), rep.get("humidity"), rep.get("battery")))
        if cur.rowcount:
            wrote.append(room)
    return wrote


def history(db=None, days=2, room=None):
    db = db or connect()
    since = H.iso(H.utcnow() - dt.timedelta(days=days))
    q = "SELECT * FROM room WHERE ts >= ?"
    args = [since]
    if room:
        q += " AND room=?"
        args.append(room)
    return db.execute(q + " ORDER BY ts", args).fetchall()


def today(db=None, hours=24):
    """Что показывает страница «Дом» под текущими цифрами: разброс за сутки по каждой комнате."""
    db = db or connect()
    since = H.iso(H.utcnow() - dt.timedelta(hours=hours))
    out = {}
    for room in HM.SENSORS:
        rows = db.execute("SELECT temperature t, humidity h FROM room WHERE room=? AND ts >= ?", (room, since)).fetchall()
        temps = [r["t"] for r in rows if r["t"] is not None]
        hums = [r["h"] for r in rows if r["h"] is not None]
        if not temps:
            continue
        out[room] = {"n": len(temps), "tmin": round(min(temps), 1), "tmax": round(max(temps), 1),
                     "hmin": round(min(hums)) if hums else None, "hmax": round(max(hums)) if hums else None}
    return out


def night_climate(db, start, end, room=BEDROOM):
    """Комната за ночь. Датчик отчитывается редко, поэтому к промежутку добавляется последний отчёт
    до его начала — иначе ровная ночь без единого отчёта осталась бы без цифр вовсе."""
    rows = db.execute("SELECT temperature t, humidity h FROM room WHERE room=? AND ts BETWEEN ? AND ? ORDER BY ts",
                      (room, start, end)).fetchall()
    before = db.execute("SELECT temperature t, humidity h FROM room WHERE room=? AND ts < ? ORDER BY ts DESC LIMIT 1",
                        (room, start)).fetchone()
    if before and before["t"] is not None:
        rows = [before] + list(rows)
    temps = [r["t"] for r in rows if r["t"] is not None]
    hums = [r["h"] for r in rows if r["h"] is not None]
    if not temps:
        return None
    return {"n": len(temps), "temp": round(st.median(temps), 1), "tmin": round(min(temps), 1), "tmax": round(max(temps), 1),
            "hum": round(st.median(hums)) if hums else None}


def nights(db=None, days=30, room=BEDROOM):
    """Ночи со стадиями сна, к каждой — какой была комната, пока человек спал."""
    db = db or connect()
    since = H.utcnow() - dt.timedelta(days=days)
    out = []
    for n in H.nights(H.rows(db, since, type_="sleep")):
        if not n.get("total"):
            continue
        n = dict(n, climate=night_climate(db, n["start"], n["end"], room))
        n["date"] = H.local(n["end"]).strftime("%Y-%m-%d")
        out.append(n)
    return sorted(out, key=lambda n: n["date"], reverse=True)


def split(rows_, value):
    """Две половины по медиане — грубо, зато честно: пока ночей мало, любая регрессия будет враньём."""
    vals = sorted(value(r) for r in rows_)
    if len(vals) < 4:
        return None
    mid = st.median(vals)
    lo = [r for r in rows_ if value(r) <= mid]
    hi = [r for r in rows_ if value(r) > mid]
    return (mid, lo, hi) if lo and hi else None


MIN_NIGHTS = 14   # меньше — не вывод, а совпадение: сравнение молчит, пока данных мало


def report(db=None, days=30, room=BEDROOM):
    db = db or connect()
    ns = nights(db, days, room)
    title = HM.SENSORS.get(room, room)
    lines = [f"Ночи за {days} дн. ({title}); «—» = датчик в эту ночь ничего не сказал.",
             "дата        сон    глубокий  REM    просыпался  комната      влажность"]
    for n in ns:
        c = n.get("climate")
        lines.append(f"{n['date'][5:]}  {H.hm(n['total']):>6}  {H.hm(n['deep']):>7}  {H.hm(n['rem']):>5}  {H.hm(n['awake']):>9}  "
                     + (f"{c['temp']:>5.1f}° {c['tmin']:.1f}–{c['tmax']:.1f}  {str(c['hum']) + '%' if c['hum'] is not None else '—':>6}" if c else f"{'—':>5}"))
    with_c = [n for n in ns if n.get("climate")]
    lines.append("")
    if len(with_c) < MIN_NIGHTS:
        lines.append(f"Ночей с показаниями: {len(with_c)}. Сравнивать тёплые и прохладные ночи рано — нужно хотя бы {MIN_NIGHTS}.")
        return "\n".join(lines)
    s = split(with_c, lambda n: n["climate"]["temp"])
    if not s:
        lines.append("Все ночи одной температуры — сравнивать нечего.")
        return "\n".join(lines)
    mid, cool, warm = s
    for label, group in ((f"Прохладные ночи (≤ {mid:.1f}°", cool), (f"Тёплые ночи (> {mid:.1f}°", warm)):
        lines.append(f"{label}, {len(group)}): сон {H.hm(H.avg([n['total'] for n in group]))}, "
                     f"глубокий {H.hm(H.avg([n['deep'] for n in group]))}, просыпался {H.hm(H.avg([n['awake'] for n in group]))}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("log"); p.add_argument("-q", "--quiet", action="store_true", help="молчать, когда нового нет (launchd)")
    p = sub.add_parser("list"); p.add_argument("--days", type=int, default=2); p.add_argument("--room")
    sub.add_parser("today")
    p = sub.add_parser("night"); p.add_argument("--days", type=int, default=30); p.add_argument("--room", default=BEDROOM)
    a = ap.parse_args(argv)
    db = connect()
    if a.cmd == "log":
        stamp = dt.datetime.now(H.tz()).strftime("%Y-%m-%d %H:%M")
        try:
            wrote = log(db)
        except Exception as e:
            # Брокер может не ответить (zigbee2mqtt перезапускается, mini только проснулся): это не повод
            # ронять агента трейсбеком каждые десять минут.
            print(f"{stamp} датчики не отвечают: {type(e).__name__}: {e}")
            return 1
        if wrote or not a.quiet:
            print(f"{stamp} записано: {', '.join(wrote) if wrote else 'ничего нового'}")
    elif a.cmd == "list":
        for r in history(db, a.days, a.room):
            print(f"{H.fmt_local(r['ts'])}  {r['room']:8} {r['temperature']:5.1f}°  {r['humidity']:5.1f}%  бат {r['battery'] or '—'}")
    elif a.cmd == "today":
        for room, v in today(db).items():
            print(f"{HM.SENSORS.get(room, room):8} {v['tmin']}–{v['tmax']}°  {v['hmin']}–{v['hmax']}%  ({v['n']} отчётов)")
    elif a.cmd == "night":
        print(report(db, a.days, a.room))


if __name__ == "__main__":
    sys.exit(main() or 0)
