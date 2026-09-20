#!/usr/bin/env python3
"""Что телефону стоит показать уведомлением (и часам — они зеркалят его сами).

Пуша нет и быть не может: Personal Team не даёт APNs, — поэтому уведомление ставит сам телефон.
Приложение BoW спрашивает `GET /api/updates` каждый раз, когда iOS его будит (HealthKit, BGTask,
открытие), и показывает то, чего ещё не видело: id уже показанного лежит у него в UserDefaults.
Отсюда и правила ниже — сервер отдаёт только то, о чём не стыдно зазвонить в эту минуту.

  Заметка о самочувствии — каждая новая (просьба пользователя 18.09.2026), но только «дневная»:
  ночную страница и так не показывает (health.daybreak), а старше REVIEW_FRESH_HOURS — уже не новость,
  телефон мог не просыпаться полдня.

  Советы Claude — одна сводка в день. Агенты на mini идут четырьмя заходами (миксы 07:20,
  фильмы/книги/лекции 07:40, статьи 07:50, французский 08:10), и уведомлять о каждом — четыре звонка
  за час; поэтому сводка собирается после RECS_SETTLE, когда все уже отработали, и живёт одним id на дату.

Usage:
  notify.py list            то, что отдаёт /api/updates
"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health as H  # noqa: E402

REVIEW_FRESH_HOURS = 3
WEEK_FRESH_HOURS = 24     # итоги недели — воскресное чтение, но телефон может проснуться и к вечеру
RECS_SETTLE = dt.time(8, 30)   # раньше сводка была бы неполной: французский приходит в 08:10
# Файл совета, ключ внутри него (у фильмов/книг/лекций один файл на три вида) и как это назвать человеку.
RECS = [
    ("mix_recs.json", None, "миксы"),
    ("taste_recs.json", "film", "фильмы"),
    ("taste_recs.json", "book", "книги"),
    ("taste_recs.json", "lecture", "лекции"),
    ("reads.json", None, "статьи"),
    ("french.json", None, "французский"),
]


def _load(name):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _local(ts):
    """`updatedAt` советов — местное время без зоны («2026-09-17T07:22:47»)."""
    try:
        return dt.datetime.fromisoformat(ts).replace(tzinfo=H.tz())
    except Exception:
        return None


def review_item(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        lr = H.last_review(H.connect())
    except Exception:
        return None
    if not lr:
        return None
    ts = H.parse_ts(lr["ts"])
    if ts < H.daybreak().astimezone(dt.timezone.utc):
        return None                                   # ночная заметка: её и страница не показывает
    if now - ts > dt.timedelta(hours=REVIEW_FRESH_HOURS):
        return None
    return {"id": "review:" + lr["ts"], "kind": "wellbeing", "title": "Well-being",
            "body": " ".join((lr["text"] or "").split()), "at": lr["ts"], "page": "health"}


def week_item(now=None):
    """Итоги недели (scripts/week.py): одно уведомление, пока они свежие."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        wk = H.last_review(H.connect(), "week")
    except Exception:
        return None
    if not wk or now - H.parse_ts(wk["ts"]) > dt.timedelta(hours=WEEK_FRESH_HOURS):
        return None
    return {"id": "week:" + wk["ts"], "kind": "week", "title": "Итоги недели",
            "body": " ".join((wk["text"] or "").split()), "at": wk["ts"], "page": "home"}


def recs_item(now=None):
    now = now or dt.datetime.now(H.tz())
    if now.timetz().replace(tzinfo=None) < RECS_SETTLE:
        return None
    fresh, newest = [], None
    for name, key, label in RECS:
        d = _load(name)
        if key:
            d = d.get(key) or {}
        ts = _local(d.get("updatedAt") or "")
        if ts and ts.date() == now.date() and d.get("items"):
            fresh.append(label)
            newest = max(newest, ts) if newest else ts
    if not fresh:
        return None
    body = ", ".join(fresh)
    return {"id": "recs:" + now.date().isoformat(), "kind": "recs", "title": "Советы на сегодня",
            "body": body[0].upper() + body[1:], "at": newest.isoformat(timespec="seconds"), "page": "site"}


def items():
    return [x for x in (review_item(), week_item(), recs_item()) if x]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] not in ("list", "-h", "--help"):
        sys.exit(__doc__)
    if "-h" in sys.argv or "--help" in sys.argv:
        sys.exit(__doc__)
    print(json.dumps({"items": items()}, ensure_ascii=False, indent=2))
