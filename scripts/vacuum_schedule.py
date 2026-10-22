#!/usr/bin/env python3
"""Ежедневная уборка и проверка ошибок Roborock для BoW.

В 10:30 МСК — пылесос, потом швабра. Календарь учитывается только с VACUUM_CALENDAR=on.

Агент просыпается в 10:00 для проверки и в 10:30 для запуска (launchd `cc.bodywithoutorgans.vacuum`,
deploy/install-vacuum-schedule.sh). Периодического опроса и повторных запусков по таймеру нет.
Для ручного run действует окно VACUUM_AT … VACUUM_AT + VACUUM_GRACE. Решение дня лежит в data/vacuum-day.json,
его показывает плашка на «Доме».

  python3 scripts/vacuum_schedule.py show            # расписание и что решено сегодня (робота не трогает)
  python3 scripts/vacuum_schedule.py run [--dry-run] # то, что делает launchd
  python3 scripts/vacuum_schedule.py run --force     # решить заново, даже если сегодня уже решено
"""
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vacuum as VC  # noqa: E402
import vacuum_monitor as VM  # noqa: E402
import schedule as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "vacuum-day.json")

ON = S.env("VACUUM_SCHEDULE", "off") == "on"   # включается на mini через .env
AT = os.environ.get("VACUUM_AT", "10:30")
DAYS = os.environ.get("VACUUM_DAYS", "0,1,2,3,4,5,6")         # пн=0 … вс=6
MODE = os.environ.get("VACUUM_MODE", "vac_then_mop")
CALENDAR = os.environ.get("VACUUM_CALENDAR", "off") == "on"
GRACE = int(os.environ.get("VACUUM_GRACE", "120"))           # минут после AT, когда ещё можно начать
CLEAN_MIN = int(os.environ.get("VACUUM_CLEAN_MIN", "60"))    # сколько длится уборка: столько календарь должен быть пуст
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def tz():
    return ZoneInfo(os.environ.get("VACUUM_TZ", "Europe/Moscow"))


def days():
    return sorted({int(x) for x in DAYS.split(",") if x.strip()})


def at_on(date, zone):
    h, m = (int(x) for x in AT.split(":"))
    return dt.datetime.combine(date, dt.time(h, m), zone)


def days_text():
    d = days()
    if d == [0, 1, 2, 3, 4]:
        return "будни"
    if d == list(range(7)):
        return "каждый день"
    return ", ".join(WEEKDAYS[i] for i in d)


def conflict(date, zone):
    """Разовое дело, которое задевает час уборки. Повторяющиеся правила («Работа», «Французский») — не
    повод: работа 10–18 и есть причина убирать в 11. Целый день (дни рождения) тоже не повод. Чужие
    тренировки (скрытые календари) не показываются нигде — и здесь не считаются."""
    import schedule as S
    start = at_on(date, zone)
    end = start + dt.timedelta(minutes=CLEAN_MIN)
    try:
        day = S.agenda(days=1, start_date=date, skip=S.hidden_calendars()).get(date, [])
    except Exception as e:
        print(f"calendar: {type(e).__name__}: {e}", flush=True)
        return None
    for e in day:
        if e["recurring"] or e["allday"]:
            continue
        if e["start"] < end and e["end"] > start:
            who = f"{e['who']}: " if e.get("who") else ""
            return f"{who}{e['summary']} {e['start']:%H:%M}"
    return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def describe(now=None):
    """Строка для плашки: расписание, режим и решение дня."""
    if not ON:
        return ""
    zone = tz()
    now = now or dt.datetime.now(zone)
    base = f"{days_text()} {AT} МСК · {VC.MODES.get(MODE, MODE).lower()}"
    st = load_state()
    if st.get("date") == now.date().isoformat() and st.get("text"):
        return f"{base} · сегодня {st['text']}"
    return base


def run(force=False, dry=False, now=None, snapshot=None):
    zone = tz()
    now = (now or dt.datetime.now(zone)).astimezone(zone)
    today = now.date()
    if not ON and not force:
        return "расписание выключено (VACUUM_SCHEDULE)"
    st = load_state()
    if now.weekday() not in days():
        return "не тот день"
    start = at_on(today, zone)
    if not (start <= now < start + dt.timedelta(minutes=GRACE)):
        return "вне окна"
    if st.get("date") == today.isoformat() and not force:
        return "сегодня уже решено: " + st.get("text", "")

    def decide(kind, text):
        print(f"{now:%F %H:%M} {kind}: {text}", flush=True)
        if not dry:
            save_state({"date": today.isoformat(), "kind": kind, "text": text, "at": now.isoformat(timespec="minutes")})
        return f"{kind}: {text}"

    why = conflict(today, zone) if CALENDAR else None
    if why:
        return decide("skip", f"пропуск — {why}")
    try:
        s = snapshot if snapshot is not None else VC.state(details=False)
    except Exception as e:
        # Робот или облако не ответили: повторного запуска по таймеру нет.
        print(f"{now:%F %H:%M} robot: {type(e).__name__}: {e}", flush=True)
        return "робот не ответил, уборка не запущена"
    last = dt.datetime.fromtimestamp(s["lastClean"], zone) if s.get("lastClean") else None
    if last and last >= start and (s.get("area") or 0) >= 5:
        return decide("skip", f"пропуск — уже убирал в {last:%H:%M}")
    if s.get("busy"):
        return decide("skip", f"пропуск — робот занят ({s['stateText']})")
    if VM.problems(s):
        return "робот или станция требуют внимания, уборка не запущена"
    if dry:
        return decide("start", f"убрал бы в {now:%H:%M}")
    try:
        result = VC.command("start", mode=MODE)
        VM.update(result, now)
        if result.get("error") or result.get("state") == "error":
            return "робот сообщил ошибку при запуске"
        if not result.get("busy"):
            return "робот не подтвердил запуск"
    except Exception as e:
        print(f"{now:%F %H:%M} start: {type(e).__name__}: {e}", flush=True)
        return "запуск не подтверждён"
    return decide("start", f"запущен в {now:%H:%M}")


def tick(now=None):
    """Один опрос на мониторинг и расписание. Ошибка связи не стирает известные проблемы."""
    now = now or dt.datetime.now(tz())
    try:
        snapshot = VC.state(details=False)
    except Exception as e:
        VM.update(now=now)
        print(f"{now:%F %H:%M} robot: {type(e).__name__}", flush=True)
        return "робот не ответил при проверке"
    VM.update(snapshot, now)
    return run(now=now, snapshot=snapshot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["show", "run", "tick"], nargs="?", default="show")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd != "run" and (a.force or a.dry_run):
        ap.error("--force и --dry-run применяются только к run")
    if a.cmd == "show":
        zone = tz()
        print(describe())
        today = dt.datetime.now(zone).date()
        for i in range(7):
            d = today + dt.timedelta(days=i)
            if d.weekday() in days():
                c = conflict(d, zone) if CALENDAR else None
                print(f"  {WEEKDAYS[d.weekday()]} {d:%d.%m} {AT}" + (f"  пропуск — {c}" if c else ""))
    elif a.cmd == "tick":
        print(tick())
    else:
        print(run(force=a.force, dry=a.dry_run))


if __name__ == "__main__":
    main()
