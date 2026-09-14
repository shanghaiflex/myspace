#!/usr/bin/env python3
"""Yandex Calendar → the day the notes are written against.

The health note knew the body and not the day: it could see a short night but not that the morning starts with a
call at nine, and it could suggest a walk during the two hours that are already taken. This reads the calendar's
private export link (read only, no OAuth) and answers two questions — what is on today, and what is free.

  python3 scripts/schedule.py fetch [--force]      # refresh the cached copy (cached CACHE_MINUTES)
  python3 scripts/schedule.py list [--days 7]      # the coming days, one line each
  python3 scripts/schedule.py digest               # today + tomorrow + free windows, for the prompt
  python3 scripts/schedule.py free [--date YYYY-MM-DD]

The link lives in .env as YANDEX_CALENDAR_ICS (Календарь → Настройки → экспорт). It carries a private token, so it
is a secret like any password: .env is gitignored and the URL is never printed. The export cannot be written to —
adding or deleting events needs CalDAV with an app password.
"""

import argparse, datetime as dt, os, re, sys, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "calendar.ics")
CACHE_MINUTES = 30
DAY_START, DAY_END = 8, 23      # the hours a free window is worth naming
FREE_MIN = 45                   # minutes below which a gap between events is not a window, it is a gap
DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_OCCURRENCES = 3000          # a guard against a daily rule that started years ago
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def env(name, default=None):
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ.get(name, default)


def tz():
    return ZoneInfo(env("WEATHER_TZ", "Europe/Moscow"))


# ---------------------------------------------------------------- fetch
def fetch(force=False):
    """The cached .ics, refreshed at most every CACHE_MINUTES. A stale copy beats no schedule at all, so a failed
    request falls back to whatever is on disk."""
    fresh = os.path.exists(CACHE) and dt.datetime.now().timestamp() - os.path.getmtime(CACHE) < CACHE_MINUTES * 60
    if fresh and not force:
        return open(CACHE, encoding="utf-8").read()
    url = env("YANDEX_CALENDAR_ICS")
    if not url:
        raise SystemExit("YANDEX_CALENDAR_ICS is not set in .env (Календарь → Настройки → экспорт)")
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            text = r.read().decode("utf-8")
        if "BEGIN:VCALENDAR" not in text:
            raise ValueError("the export did not return a calendar")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            f.write(text)
        return text
    except Exception as e:
        if os.path.exists(CACHE):
            print(f"calendar: {e}; using the cached copy from {dt.datetime.fromtimestamp(os.path.getmtime(CACHE)):%d.%m %H:%M}", file=sys.stderr)
            return open(CACHE, encoding="utf-8").read()
        raise


# ---------------------------------------------------------------- parse
def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))


def parse_props(block):
    """Every property of one component as {NAME: [(params, value)]} — a property can repeat (EXDATE, ATTENDEE)."""
    props = {}
    for line in block.split("\n"):
        if not line or ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        params = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        props.setdefault(parts[0].upper(), []).append((params, value))
    return props


def first(props, name, default=""):
    return props[name][0][1] if name in props else default


def unescape(s):
    return s.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse_dt(params, value, zone):
    """A calendar time comes in three shapes: a date (an all-day event), a local time with a TZID, and UTC."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return dt.datetime.strptime(value, "%Y%m%d").replace(tzinfo=zone), True
    if value.endswith("Z"):
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone(zone), False
    try:
        where = ZoneInfo(params["TZID"]) if params.get("TZID") else zone
    except Exception:
        where = zone
    return dt.datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=where).astimezone(zone), False


def parse_rrule(value):
    return dict(p.split("=", 1) for p in value.split(";") if "=" in p)


def events(text, zone):
    """Every VEVENT as a dict. Overrides of a single occurrence (RECURRENCE-ID) are kept apart: they cancel or
    replace one instance of the series they belong to."""
    out, overrides = [], {}
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfold(text), re.S):
        p = parse_props(block)
        if "DTSTART" not in p:
            continue
        start, allday = parse_dt(*p["DTSTART"][0], zone)
        if "DTEND" in p:
            end, _ = parse_dt(*p["DTEND"][0], zone)
        elif "DURATION" in p:
            end = start + parse_duration(first(p, "DURATION"))
        else:
            end = start + dt.timedelta(days=1 if allday else 1 / 24)
        ev = {"uid": first(p, "UID"), "summary": unescape(first(p, "SUMMARY")) or "(без названия)",
              "start": start, "end": end, "allday": allday,
              "rrule": parse_rrule(first(p, "RRULE")) if "RRULE" in p else None,
              "exdate": {parse_dt(prm, one, zone)[0] for prm, val in p.get("EXDATE", []) for one in val.split(",")},
              "cancelled": first(p, "STATUS").upper() == "CANCELLED",
              "busy": first(p, "TRANSP").upper() != "TRANSPARENT"}
        if "RECURRENCE-ID" in p:
            overrides[(ev["uid"], parse_dt(*p["RECURRENCE-ID"][0], zone)[0])] = ev
        else:
            out.append(ev)
    return out, overrides


def parse_duration(value):
    m = re.fullmatch(r"[+-]?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return dt.timedelta(hours=1)
    w, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


# ---------------------------------------------------------------- recurrence
def occurrences(ev, window_start, window_end):
    """The starts of a series that fall inside the window. Only the shapes a personal calendar actually uses are
    expanded — weekly (the whole calendar here), daily, monthly by date or by n-th weekday, yearly."""
    r = ev["rrule"]
    if not r:
        return [ev["start"]] if ev["start"] < window_end and ev["end"] > window_start else []
    freq, interval = r.get("FREQ", "WEEKLY"), int(r.get("INTERVAL", 1) or 1)
    until = None
    if r.get("UNTIL"):
        until, _ = parse_dt({}, r["UNTIL"], ev["start"].tzinfo)
    count, start, out, seen = int(r["COUNT"]) if r.get("COUNT") else None, ev["start"], [], 0
    stop = min(window_end, until + dt.timedelta(days=1)) if until else window_end

    def emit(when):
        nonlocal seen
        seen += 1
        if until and when > until:
            return False
        if count and seen > count:
            return False
        if when >= window_start and when < window_end and when not in ev["exdate"]:
            out.append(when)
        return True

    if freq == "WEEKLY":
        days = sorted(WEEKDAYS[d[-2:]] for d in r.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS) or [start.weekday()]
        week = start - dt.timedelta(days=start.weekday())
        for _ in range(MAX_OCCURRENCES):
            if week > stop:
                break
            for wd in days:
                when = week + dt.timedelta(days=wd)
                if when >= start and not emit(when):
                    return out
            week += dt.timedelta(weeks=interval)
    elif freq == "DAILY":
        when = start
        for _ in range(MAX_OCCURRENCES):
            if when > stop or not emit(when):
                break
            when += dt.timedelta(days=interval)
    elif freq in ("MONTHLY", "YEARLY"):
        year, month = start.year, start.month
        for _ in range(MAX_OCCURRENCES):
            when = month_occurrence(start, year, month, r)
            if when and when >= start:
                if when > stop:
                    break
                if not emit(when):
                    break
            step = interval if freq == "MONTHLY" else interval * 12
            month += step
            year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
            if year > stop.year + 1:
                break
    return out


def month_occurrence(start, year, month, r):
    """The day this month's instance falls on: BYMONTHDAY, or an n-th weekday (BYDAY=3FR), or the day of DTSTART."""
    try:
        if r.get("BYDAY") and re.fullmatch(r"-?\d[A-Z]{2}", r["BYDAY"]):
            n, wd = int(r["BYDAY"][:-2]), WEEKDAYS[r["BYDAY"][-2:]]
            days = [d for d in month_days(year, month) if d.weekday() == wd]
            day = days[n - 1] if n > 0 else days[n]
            return start.replace(year=year, month=month, day=day.day)
        day = int(r["BYMONTHDAY"]) if r.get("BYMONTHDAY") else start.day
        return start.replace(year=year, month=month, day=day)
    except (ValueError, IndexError):
        return None   # the 31st of a month that has 30 days


def month_days(year, month):
    d = dt.date(year, month, 1)
    while d.month == month:
        yield d
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------- agenda
def agenda(days=7, start_date=None, text=None):
    """Day by day: what is on, in order. Cancelled instances and events marked free (TRANSP) are left out."""
    zone = tz()
    text = text or fetch()
    evs, overrides = events(text, zone)
    today = start_date or dt.datetime.now(zone).date()
    window_start = dt.datetime.combine(today, dt.time(0), zone)
    window_end = window_start + dt.timedelta(days=days)
    out = {today + dt.timedelta(days=i): [] for i in range(days)}
    for ev in evs:
        for when in occurrences(ev, window_start, window_end):
            item = overrides.get((ev["uid"], when), ev)
            if item["cancelled"]:
                continue
            start = item["start"] if item is not ev else when
            end = start + (item["end"] - item["start"])
            if start.date() in out:
                # An event the calendar shows as free (TRANSP) still pins an hour of the day — the lesson happens —
                # it just does not make the person busy, so it is listed but never eats a free window.
                out[start.date()].append({"summary": item["summary"], "start": start, "end": end,
                                          "allday": item["allday"], "busy": item["busy"]})
    for day in out.values():
        day.sort(key=lambda e: (not e["allday"], e["start"]))
    return out


def free_windows(day_events, date, zone):
    """The gaps worth naming between the events of one day, inside DAY_START..DAY_END."""
    day_start = dt.datetime.combine(date, dt.time(DAY_START), zone)
    day_end = dt.datetime.combine(date, dt.time(DAY_END), zone)
    busy = sorted((max(e["start"], day_start), min(e["end"], day_end)) for e in day_events
                  if e["busy"] and not e["allday"] and e["end"] > day_start and e["start"] < day_end)
    out, cursor = [], day_start
    for start, end in busy:
        if start - cursor >= dt.timedelta(minutes=FREE_MIN):
            out.append((cursor, start))
        cursor = max(cursor, end)
    if day_end - cursor >= dt.timedelta(minutes=FREE_MIN):
        out.append((cursor, day_end))
    return out


def fmt_event(e):
    return ("весь день " if e["allday"] else f"{e['start']:%H:%M}–{e['end']:%H:%M} ") + e["summary"] + ("" if e["busy"] else " (не занимает)")


def fmt_day(date, day_events):
    return f"{DOW[date.weekday()]} {date:%d.%m}: " + ("; ".join(fmt_event(e) for e in day_events) or "пусто")


def digest(days=2):
    """The block the health note is given: today, tomorrow, and where the day has room."""
    zone = tz()
    now = dt.datetime.now(zone)
    days_ = agenda(days=max(days, 2))
    dates = sorted(days_)
    lines = ["Расписание (Яндекс.Календарь):"]
    for i, date in enumerate(dates[:days]):
        label = {0: "сегодня", 1: "завтра"}.get(i, "")
        lines.append(f"- {label} {fmt_day(date, days_[date])}".replace("  ", " "))
    rest = [(s, e) for s, e in free_windows(days_[dates[0]], dates[0], zone) if e > now]
    if rest:
        windows = ", ".join(f"{max(s, now):%H:%M}–{e:%H:%M}" for s, e in rest)
        lines.append(f"- свободно сегодня: {windows}")
    first_tomorrow = next((e for e in days_[dates[1]] if not e["allday"]), None)
    if first_tomorrow:
        lines.append(f"- завтра начинается в {first_tomorrow['start']:%H:%M} ({first_tomorrow['summary']})")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("list"); p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("digest"); p.add_argument("--days", type=int, default=2)
    p = sub.add_parser("free"); p.add_argument("--date")
    a = ap.parse_args()
    if a.cmd == "fetch":
        text = fetch(force=a.force)
        evs, _ = events(text, tz())
        print(f"{len(text)} bytes, {len(evs)} событий, {sum(1 for e in evs if e['rrule'])} из них повторяются")
    elif a.cmd == "list":
        for date, day in sorted(agenda(a.days).items()):
            print(fmt_day(date, day))
    elif a.cmd == "digest":
        print(digest(a.days))
    elif a.cmd == "free":
        zone = tz()
        date = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(zone).date()
        days_ = agenda(1, start_date=date)
        for s, e in free_windows(days_[date], date, zone):
            print(f"{s:%H:%M}–{e:%H:%M}")


if __name__ == "__main__":
    main()
