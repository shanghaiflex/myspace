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

import argparse, datetime as dt, json, os, re, sys, urllib.error, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "calendar.ics")
CACHE_MINUTES = 30
DAY_START, DAY_END = 8, 23      # the hours a free window is worth naming
# Calendars shown in the day but not counted as busy. «Тренировки» holds someone else's swimming — four hours of
# every Monday and Friday morning — and taking it as occupied left the day with no room in it at all.
FREE_CALENDARS = "Тренировки"
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


def only_calendars():
    """The calendars the health note is allowed to see (SCHEDULE_CALENDARS); empty means all of them.

    The cache holds every collection — «Семья» is what the page shows — so the narrowing happens here,
    at the reader, and not at fetch time: the model gets one calendar, the page gets the day."""
    return [c.strip() for c in (env("SCHEDULE_CALENDARS") or "").split(",") if c.strip()]


def free_calendars():
    return [c.strip() for c in env("SCHEDULE_FREE_CALENDARS", FREE_CALENDARS).split(",") if c.strip()]


# ---------------------------------------------------------------- fetch
def fetch(force=False):
    """Everything the account has, cached for CACHE_MINUTES.

    Over CalDAV, because the export link publishes exactly one calendar: it pointed at «Мои события», which was
    abandoned in April, while the day actually lives in «Семья» — the note was reading a calendar nobody keeps.
    Without a password the export link still works as a fallback, and a stale copy beats no schedule at all."""
    fresh = os.path.exists(CACHE) and dt.datetime.now().timestamp() - os.path.getmtime(CACHE) < CACHE_MINUTES * 60
    if fresh and not force:
        return open(CACHE, encoding="utf-8").read()
    try:
        text = caldav_ics() if env("YANDEX_CALDAV_PASSWORD") else export_ics()
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            f.write(text)
        return text
    except Exception as e:
        if os.path.exists(CACHE):
            print(f"calendar: {e}; using the cached copy from {dt.datetime.fromtimestamp(os.path.getmtime(CACHE)):%d.%m %H:%M}", file=sys.stderr)
            return open(CACHE, encoding="utf-8").read()
        raise


def export_ics():
    url = env("YANDEX_CALENDAR_ICS")
    if not url:
        raise SystemExit("neither YANDEX_CALDAV_PASSWORD nor YANDEX_CALENDAR_ICS is set in .env")
    with urllib.request.urlopen(url, timeout=25) as r:
        text = r.read().decode("utf-8")
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("the export did not return a calendar")
    return text


def caldav_ics():
    """Every event of every calendar collection, one file. Each event is stamped with the calendar it came from —
    CATEGORIES would be the natural place, but Yandex fills it in for one calendar and leaves it empty in the rest."""
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//bodywithoutorgans//schedule.py//RU"]
    for c in calendars():
        if "/todos-" in c["href"]:
            continue
        for e in calendar_events(c["href"]):
            block = re.search(r"BEGIN:VEVENT.*?END:VEVENT", unfold(e["ics"]), re.S)
            if block:
                out.append(block.group(0).replace("BEGIN:VEVENT", f"BEGIN:VEVENT\nX-BOW-CALENDAR:{c['name']}", 1))
    out.append("END:VCALENDAR")
    return "\n".join(out)


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
              "calendar": unescape(first(p, "X-BOW-CALENDAR") or first(p, "CATEGORIES")),
              "cancelled": first(p, "STATUS").upper() == "CANCELLED",
              "busy": first(p, "TRANSP").upper() != "TRANSPARENT"}
        ev["busy"] = ev["busy"] and ev["calendar"] not in free_calendars()
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
def agenda(days=7, start_date=None, text=None, only=None, skip=None):
    """Day by day: what is on, in order. Cancelled instances and events marked free (TRANSP) are left out.

    `only` keeps just those calendars (the note reads one), `skip` drops some (the page has no use for four
    hours of someone else's swimming)."""
    zone = tz()
    text = text or fetch()
    evs, overrides = events(text, zone)
    if only:
        evs = [e for e in evs if e["calendar"] in only]
    if skip:
        evs = [e for e in evs if e["calendar"] not in skip]
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
                                          "allday": item["allday"], "busy": item["busy"],
                                          "calendar": item["calendar"]})
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


def fmt_event(e, calendar=False):
    return (("весь день " if e["allday"] else f"{e['start']:%H:%M}–{e['end']:%H:%M} ") + e["summary"]
            + (f" [{e['calendar']}]" if calendar and e["calendar"] else "") + ("" if e["busy"] else " (не занимает)"))


def fmt_day(date, day_events, calendar=False):
    return f"{DOW[date.weekday()]} {date:%d.%m}: " + ("; ".join(fmt_event(e, calendar) for e in day_events) or "пусто")


def digest(days=2):
    """The block the health note is given: today, tomorrow, and where the day has room."""
    zone = tz()
    now = dt.datetime.now(zone)
    days_ = agenda(days=max(days, 2), only=only_calendars())
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


def day_plan(date=None):
    """Дела на сегодня — то, что рисует плашка на главной (и в приложении: там та же страница).

    В отличие от дайджеста здесь все календари, кроме чужих тренировок: врачи и няня из «Семьи» — это дела дня,
    а четыре часа плавания Полины делом не являются. Времена уезжают и строкой (для подписи), и ISO
    (чтобы страница сама решила, что уже прошло)."""
    zone = tz()
    now = dt.datetime.now(zone)
    days_ = agenda(days=2, start_date=date, skip=free_calendars())
    dates = sorted(days_)
    today, tomorrow = dates[0], dates[1]

    def item(e):
        return {"title": e["summary"], "allday": e["allday"], "busy": e["busy"], "calendar": e["calendar"] or None,
                "start": e["start"].isoformat(), "end": e["end"].isoformat(),
                "from": None if e["allday"] else f"{e['start']:%H:%M}", "to": None if e["allday"] else f"{e['end']:%H:%M}"}

    free = []
    for s_, e_ in free_windows(days_[today], today, zone):
        s_ = max(s_, now)
        if e_ - s_ >= dt.timedelta(minutes=FREE_MIN):
            free.append({"from": f"{s_:%H:%M}", "to": f"{e_:%H:%M}", "minutes": round((e_ - s_).total_seconds() / 60)})
    first = next((e for e in days_[tomorrow] if not e["allday"]), None)
    return {"date": today.isoformat(), "now": now.isoformat(), "dow": DOW[today.weekday()],
            "events": [item(e) for e in days_[today]], "free": free,
            "tomorrow": {"title": first["summary"], "from": f"{first['start']:%H:%M}"} if first else None}


# ---------------------------------------------------------------- caldav (writing)
# The export link is read only. Changing the calendar goes through CalDAV with an app password
# (id.yandex.ru → Безопасность → Пароли приложений → Календарь): YANDEX_CALDAV_USER / YANDEX_CALDAV_PASSWORD in .env.
CALDAV_URL = "https://caldav.yandex.ru"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}


def dav(method, url, body=None, depth=None, headers=None):
    user, password = env("YANDEX_CALDAV_USER"), env("YANDEX_CALDAV_PASSWORD")
    if not user or not password:
        raise SystemExit("YANDEX_CALDAV_USER / YANDEX_CALDAV_PASSWORD are not set in .env "
                         "(id.yandex.ru → Безопасность → Пароли приложений → Календарь)")
    import base64
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode())
    if body:
        req.add_header("Content-Type", "application/xml; charset=utf-8" if body.lstrip().startswith("<") else "text/calendar; charset=utf-8")
    if depth is not None:
        req.add_header("Depth", str(depth))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def dav_tree(body):
    import xml.etree.ElementTree as ET
    return ET.fromstring(body)


def calendars():
    """The calendar collections of the account, discovered the standard way and falling back to the path Yandex
    actually uses if a step of the discovery is not answered."""
    import urllib.parse
    status, body, _ = dav("PROPFIND", CALDAV_URL + "/", depth=0,
                          body='<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')
    principal = ""
    if status in (207, 200):
        el = dav_tree(body).find(".//d:current-user-principal/d:href", NS)
        principal = el.text if el is not None else ""
    home = ""
    if principal:
        status, body, _ = dav("PROPFIND", urllib.parse.urljoin(CALDAV_URL, principal), depth=0,
                              body='<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>')
        el = dav_tree(body).find(".//c:calendar-home-set/d:href", NS) if status in (207, 200) else None
        home = el.text if el is not None else ""
    home = home or f"/calendars/{env('YANDEX_CALDAV_USER', '').split('@')[0]}/"
    status, body, _ = dav("PROPFIND", urllib.parse.urljoin(CALDAV_URL, home), depth=1,
                          body='<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>')
    if status not in (207, 200):
        raise SystemExit(f"CalDAV {status}: {body[:200]}")
    out = []
    for resp in dav_tree(body).findall(".//d:response", NS):
        href = resp.find("d:href", NS)
        if resp.find(".//d:resourcetype/c:calendar", NS) is not None and href is not None:
            name = resp.find(".//d:displayname", NS)
            out.append({"href": href.text, "name": (name.text if name is not None else "") or href.text})
    return out


def calendar_events(href):
    """Every VEVENT of one collection with its href and etag — enough to change one in place."""
    import urllib.parse
    body = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/>'
            '<c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT"/></c:comp-filter></c:filter></c:calendar-query>')
    status, xml, _ = dav("REPORT", urllib.parse.urljoin(CALDAV_URL, href), body=body, depth=1)
    if status not in (207, 200):
        raise SystemExit(f"CalDAV REPORT {status}: {xml[:200]}")
    out = []
    for resp in dav_tree(xml).findall(".//d:response", NS):
        data = resp.find(".//c:calendar-data", NS)
        if data is None or not data.text:
            continue
        p = parse_props(re.search(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfold(data.text), re.S).group(1)) \
            if "BEGIN:VEVENT" in data.text else {}
        out.append({"href": resp.find("d:href", NS).text, "etag": (resp.find(".//d:getetag", NS).text or "").strip(),
                    "ics": data.text, "summary": unescape(first(p, "SUMMARY")), "rrule": first(p, "RRULE"),
                    "dtstart": first(p, "DTSTART"), "uid": first(p, "UID")})
    return out


def put_event(href, ics, etag=None):
    import urllib.parse
    headers = {"If-Match": etag} if etag else {"If-None-Match": "*"}
    status, body, _ = dav("PUT", urllib.parse.urljoin(CALDAV_URL, href), body=ics, headers=headers)
    if status not in (200, 201, 204):
        raise SystemExit(f"CalDAV PUT {status}: {body[:300]}")
    return status


def set_rrule(ics, rule):
    """Replace the rule of the event itself. Only inside the VEVENT: the VTIMEZONE block that comes before it in
    the file carries seven RRULE lines of its own — the daylight-saving history of the zone — and replacing «the
    first RRULE in the file» rewrites one of those while the event keeps its old rule."""
    body = re.sub(r"^RRULE:.*$", "RRULE:" + rule, re.search(r"BEGIN:VEVENT.*?END:VEVENT", ics, re.S).group(0),
                  count=1, flags=re.M)
    return re.sub(r"BEGIN:VEVENT.*?END:VEVENT", lambda _: body, ics, count=1, flags=re.S)


def end_rule(events_, title, until_date, dry_run=False, at=None):
    """Close a repeating rule with UNTIL instead of deleting it: the past stays in the calendar, the future stops.
    Deleting the series would take the history with it, and history is the only record of what was actually done."""
    stamp = until_date.strftime("%Y%m%dT235959Z")
    done = []
    # A title alone is not an identifier: «ЛФК» matched both the rule being retired and the replacement created a
    # minute earlier, and closed them both. Where several rules answer to the name, the hour has to be given.
    matches = [e for e in events_ if e["rrule"] and title.lower() in (e["summary"] or "").lower()
               and (not at or e["dtstart"][-6:-4] + ":" + e["dtstart"][-4:-2] == at)]
    if len(matches) > 1:
        raise SystemExit("под это название подходит несколько правил, уточни время через --at HH:MM:\n  "
                         + "\n  ".join(f"{e['summary']} {e['dtstart'][-6:-4]}:{e['dtstart'][-4:-2]}  {e['rrule']}" for e in matches))
    for e in matches:
        rule = re.sub(r";?UNTIL=[0-9TZ]+", "", e["rrule"]) + f";UNTIL={stamp}"
        done.append((e["summary"], e["rrule"], rule))
        if not dry_run:
            ics = bump_sequence(set_rrule(e["ics"].replace("\r\n", "\n"), rule))
            put_event(e["href"], ics.replace("\n", "\r\n"), e["etag"])
    return done


def remove_rule(events_, title, dry_run=False, at=None, every=False):
    """Delete an event outright. Unlike `end` this takes the past with it, so every deleted event is first written
    to data/calendar-deleted/ — a rule the calendar no longer has is otherwise unrecoverable."""
    matches = [e for e in events_ if e["rrule"] and title.lower() in (e["summary"] or "").lower()
               and (not at or e["dtstart"][-6:-4] + ":" + e["dtstart"][-4:-2] == at)]
    if len(matches) > 1 and not every:
        raise SystemExit("под это название подходит несколько правил, уточни --at HH:MM или разреши --all:\n  "
                         + "\n  ".join(f"{e['summary']} {e['dtstart'][-6:-4]}:{e['dtstart'][-4:-2]}  {e['rrule']}" for e in matches))
    done = []
    for e in matches:
        done.append((e["summary"], e["dtstart"][-6:-4] + ":" + e["dtstart"][-4:-2], e["rrule"]))
        if not dry_run:
            backup = os.path.join(ROOT, "data", "calendar-deleted")
            os.makedirs(backup, exist_ok=True)
            name = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{e['summary'].replace('/', '-')}.ics"
            with open(os.path.join(backup, name), "w", encoding="utf-8") as f:
                f.write(e["ics"])
            import urllib.parse
            status, body, _ = dav("DELETE", urllib.parse.urljoin(CALDAV_URL, e["href"]),
                                  headers={"If-Match": e["etag"]} if e["etag"] else None)
            if status not in (200, 204, 404):
                raise SystemExit(f"CalDAV DELETE {status}: {body[:200]}")
    return done


def bump_sequence(ics):
    """A changed event needs a higher SEQUENCE and a fresh DTSTAMP, or clients may keep showing the old one."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics = re.sub(r"^DTSTAMP:.*$", f"DTSTAMP:{now}", ics, count=1, flags=re.M)
    m = re.search(r"^SEQUENCE:(\d+)$", ics, re.M)
    if m:
        return re.sub(r"^SEQUENCE:\d+$", f"SEQUENCE:{int(m.group(1)) + 1}", ics, count=1, flags=re.M)
    return ics.replace("BEGIN:VEVENT\n", "BEGIN:VEVENT\nSEQUENCE:1\n", 1)


def build_event(summary, start, minutes, byday=None, zone=None):
    """A minimal VEVENT. Times are written with a TZID, so the event keeps its wall-clock hour."""
    import uuid
    zone = zone or tz()
    end = start + dt.timedelta(minutes=minutes)
    uid = f"{uuid.uuid4()}@bodywithoutorgans.cc"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//bodywithoutorgans//schedule.py//RU", "BEGIN:VEVENT",
             f"UID:{uid}", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}",
             f"DTSTART;TZID={zone.key}:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID={zone.key}:{end:%Y%m%dT%H%M%S}",
             f"SUMMARY:{summary}"]
    if byday:
        lines.append(f"RRULE:FREQ=WEEKLY;BYDAY={','.join(byday)};INTERVAL=1")
    lines += ["TRANSP:OPAQUE", "END:VEVENT", "END:VCALENDAR"]
    return uid, "\r\n".join(lines) + "\r\n"


def next_weekday(day, code):
    """The first `code` (MO..SU) on or after `day`."""
    return day + dt.timedelta(days=(WEEKDAYS[code] - day.weekday()) % 7)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("list"); p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("digest"); p.add_argument("--days", type=int, default=2)
    p = sub.add_parser("today"); p.add_argument("--date")
    p = sub.add_parser("free"); p.add_argument("--date")
    sub.add_parser("calendars")
    sub.add_parser("rules")
    p = sub.add_parser("end"); p.add_argument("title"); p.add_argument("--date"); p.add_argument("--at", help="HH:MM, если правил с таким названием несколько")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("remove"); p.add_argument("title"); p.add_argument("--at", help="HH:MM")
    p.add_argument("--all", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("add"); p.add_argument("title"); p.add_argument("--day", required=True, help="MO..SU")
    p.add_argument("--time", required=True, help="HH:MM"); p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--once", action="store_true", help="одно событие, а не еженедельное правило")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "fetch":
        text = fetch(force=a.force)
        evs, _ = events(text, tz())
        print(f"{len(text)} bytes, {len(evs)} событий, {sum(1 for e in evs if e['rrule'])} из них повторяются")
    elif a.cmd == "list":
        for date, day in sorted(agenda(a.days).items()):
            print(fmt_day(date, day, calendar=True))
    elif a.cmd == "digest":
        print(digest(a.days))
    elif a.cmd == "today":
        print(json.dumps(day_plan(dt.date.fromisoformat(a.date) if a.date else None), ensure_ascii=False, indent=2))
    elif a.cmd == "calendars":
        for c in calendars():
            print(f"{c['name']}\t{c['href']}")
    elif a.cmd == "rules":
        for c in calendars():
            for e in calendar_events(c["href"]):
                if e["rrule"]:
                    print(f"{e['summary']:24} {e['dtstart'][-13:]}  {e['rrule']}\t{e['href']}")
    elif a.cmd == "end":
        until = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(tz()).date() - dt.timedelta(days=1)
        for c in calendars():
            for summary, was, now_ in end_rule(calendar_events(c["href"]), a.title, until, a.dry_run, a.at):
                print(("(сухой прогон) " if a.dry_run else "") + f"{summary}: {was} → {now_}")
    elif a.cmd == "remove":
        for c in calendars():
            if "/todos-" in c["href"]:
                continue
            for summary, at, rule in remove_rule(calendar_events(c["href"]), a.title, a.dry_run, a.at, a.all):
                print(("(сухой прогон) " if a.dry_run else "удалено: ") + f"{summary} {at}  {rule}")
    elif a.cmd == "add":
        zone = tz()
        hh, mm = (int(x) for x in a.time.split(":"))
        day = next_weekday(dt.datetime.now(zone).date(), a.day.upper())
        start = dt.datetime.combine(day, dt.time(hh, mm), zone)
        uid, ics = build_event(a.title, start, a.minutes, None if a.once else [a.day.upper()], zone)
        # Into my own calendar, not whichever collection the server happens to list first.
        only = [c.strip() for c in (env("SCHEDULE_CALENDARS") or "").split(",") if c.strip()]
        cals = calendars()
        mine = next((c for c in cals if c["name"] in only), None) or cals[0]
        target = mine["href"].rstrip("/") + f"/{uid}.ics"
        print(("(сухой прогон) " if a.dry_run else "") + f"{a.title}: {start:%a %d.%m %H:%M}"
              + (f" еженедельно {a.day.upper()}" if not a.once else "") + f", {a.minutes} мин")
        if not a.dry_run:
            put_event(target, ics)
    elif a.cmd == "free":
        zone = tz()
        date = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(zone).date()
        days_ = agenda(1, start_date=date, only=only_calendars())
        for s, e in free_windows(days_[date], date, zone):
            print(f"{s:%H:%M}–{e:%H:%M}")


if __name__ == "__main__":
    main()
