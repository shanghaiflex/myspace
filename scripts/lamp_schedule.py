#!/usr/bin/env python3
"""Вечерний свет по солнцу, а не по будильнику.

Раньше расписание висело в launchd тремя жёсткими временами (19:00 уютно, 22:00 ночник, 23:00 выкл) и жило
вне репозитория — в `~/mqtt` на mini. Беда в том, что 19:00 — это не вечер, а число: в декабре темнеет в
16:00 и три часа сидишь при верхнем свете, в июне в 19:00 ещё день и лампы горят впустую. Теперь «уютно»
привязано к закату (`sun.py`, астрономия без сети), а «ночник» и «выключить» остались по часам: они про сон,
а не про солнце.

Агент просыпается каждые STEP минут и смотрит, в какой фазе дня он сейчас. Свет переключается **только на
границе фазы** — внутри фазы лампы не трогаются, поэтому выключенный вручную в девять вечера свет так и
останется выключенным до ночника. Отдельно проверяется перезагрузка: если mini поднялся уже после того, как
фаза была применена (лампы после сбоя питания загораются сами и не тем), фаза применяется заново — это и есть
прежний `lamp-catchup.sh`.

  python3 scripts/lamp_schedule.py show       # что и когда сегодня (ничего не трогает)
  python3 scripts/lamp_schedule.py run        # применить фазу, если она сменилась (launchd)
  python3 scripts/lamp_schedule.py run --force  # применить текущую фазу в любом случае
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import home as HM  # noqa: E402
import sun as SUN  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "lamp-phase.json")

# Лампы зажигаются до заката, а не в закат: в комнате темнеет раньше, чем солнце уходит за горизонт.
OFFSET = int(os.environ.get("LAMP_EVENING_OFFSET", "-60"))       # минуты к закату: за полчаса до заката в пасмурный сентябрь уже темно (21.09.2026)
EARLIEST = os.environ.get("LAMP_EVENING_EARLIEST", "15:30")      # зимой закат в 15:57 — раньше не надо
LATEST = os.environ.get("LAMP_EVENING_LATEST", "21:00")          # летом закат в 21:17 — позже смысла нет
NIGHT_AT = os.environ.get("LAMP_NIGHT_AT", "22:00")              # ночник — про сон, не про солнце
OFF_AT = os.environ.get("LAMP_OFF_AT", "23:00")


def hhmm(s, date, zone):
    h, m = (int(x) for x in str(s).split(":"))
    return dt.datetime.combine(date, dt.time(h, m), zone)


def phases(date=None, zone=None):
    """Границы сегодняшнего дня: (id, время, человеческое название)."""
    zone = zone or SUN.tz()
    date = date or dt.datetime.now(zone).date()
    set_ = SUN.sunset(date, zone=zone)
    evening = (set_ + dt.timedelta(minutes=OFFSET)) if set_ else hhmm("19:00", date, zone)
    evening = min(max(evening, hhmm(EARLIEST, date, zone)), hhmm(LATEST, date, zone))
    return [("evening", evening, HM.SCENES["cozy"]["title"]),
            ("night", hhmm(NIGHT_AT, date, zone), HM.SCENES["night"]["title"]),
            ("off", hhmm(OFF_AT, date, zone), "выключить")]


def phase_at(now=None, zone=None):
    """Фаза, в которой мы сейчас. `day` — утро и день: свет не трогаем вовсе, в том числе после
    перезагрузки (так же вёл себя и прежний lamp-catchup.sh: вне вечера он молчал)."""
    zone = zone or SUN.tz()
    now = now or dt.datetime.now(zone)
    cur = ("day", None, "день")
    for p in phases(now.date(), zone):
        if p[1] <= now:
            cur = p
    return cur


# Что именно шлётся лампам. Цвет и яркость берутся из каталога сцен (`home.SCENES`) — он единственный
# источник правды, здесь только своё время перехода: вечером и ночью плавно, на выключение совсем мягко.
def payloads(phase):
    if phase == "evening":
        return [("lamps", {**HM.SCENES["cozy"]["payload"], "transition": 30}), ("plug", {"state": "ON"})]
    if phase == "night":
        return [("lamps", {**HM.SCENES["night"]["payload"], "transition": 30})]
    if phase == "off":
        return [("lamps", {"state": "OFF", "transition": 60}), ("plug", {"state": "OFF"})]
    return []


def boot_time():
    """Когда mini включился. Фазу, применённую до перезагрузки, надо применить заново: после сбоя питания
    лампы просыпаются сами и не в той сцене."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"sec\s*=\s*(\d+)", out)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def run(force=False, dry=False, now=None):
    zone = SUN.tz()
    now = now or dt.datetime.now(zone)
    phase, at, title = phase_at(now, zone)
    today = now.date().isoformat()
    st = load_state()
    rebooted = st.get("appliedAt", 0) < boot_time()
    same = st.get("date") == today and st.get("phase") == phase
    if same and not force and not rebooted:
        return None
    why = "перезагрузка" if rebooted and same else "смена фазы"
    if dry:
        return f"{now:%H:%M} → {phase} ({title or '—'}), причина: {'force' if force else why}"
    wait = 0
    for target, body in payloads(phase):
        HM.set_device(target, body)
        wait = max(wait, float(body.get("transition", 0)))
    # `set_device` проверяет результат в отдельном потоке (групповая команда — широковещание без
    # подтверждения): процесс не должен умереть раньше проверки.
    if wait:
        time.sleep(wait + HM.VERIFY_GRACE + 2)
    save_state({"date": today, "phase": phase, "appliedAt": int(time.time()), "at": at.isoformat() if at else None})
    return f"{now:%H:%M} {phase}" + (f" — {title}" if title else "") + f" ({why})"


def describe(date=None):
    """Строка для страницы «Дом»: во сколько сегодня что, и от какого заката это посчитано."""
    zone = SUN.tz()
    date = date or dt.datetime.now(zone).date()
    set_ = SUN.sunset(date, zone=zone)
    parts = [f"{t:%H:%M} {title}" for _, t, title in phases(date, zone)]
    return (f"закат {set_:%H:%M} · " if set_ else "") + " · ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("run")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        zone = SUN.tz()
        now = dt.datetime.now(zone)
        print(describe())
        print(f"сейчас {now:%H:%M} — фаза {phase_at(now, zone)[0]}; записано: {load_state() or '—'}")
        return 0
    stamp = dt.datetime.now(SUN.tz()).strftime("%Y-%m-%d %H:%M")
    try:
        out = run(force=a.force, dry=a.dry_run)
    except Exception as e:
        print(f"{stamp} свет не переключился: {type(e).__name__}: {e}")
        return 1
    if out:
        print(f"{stamp[:10]} {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
