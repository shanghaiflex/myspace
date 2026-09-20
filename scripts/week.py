#!/usr/bin/env python3
"""Итоги недели (20.09.2026): единственное место, где все данные сайта сходятся разом.

Часовая заметка о самочувствии смотрит на день; здесь — неделя целиком и со всех сторон: тело
(сон, пульс, HRV, шаги, тренировки по дисциплинам против целей), что слушал, читал и смотрел, что
отвечал советам, куда ушли деньги (чеки), что впереди по календарю — и ночи против вечеров
(scripts/nights.py), когда их накопилось достаточно. Всё это уходит одним дайджестом в Claude
(health/WEEK.md), ответ ложится в health.db как review kind=week и показывается на главной.

  python3 scripts/week.py digest      # то, что видит модель
  python3 scripts/week.py             # то же
  sh scripts/week_review.sh           # спросить сейчас (на mini: ssh mini 'cd movies && sh scripts/week_review.sh')
"""
import collections
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health as H  # noqa: E402
import lectures as L  # noqa: E402
import mixes as X  # noqa: E402
import books as B  # noqa: E402
import movies as M  # noqa: E402
import nights as NI  # noqa: E402
import schedule as S  # noqa: E402

DAYS = 7
GOAL_KINDS = ("плавание", "силовая", "бег", "велосипед")   # дисциплины из health/goals.md
ROUTINE = ("работа",)


def hm(minutes):
    return H.hm(minutes)


def _load(name):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _in(when, start, end):
    """Дата (ISO-строка или unix-время) внутри [start, end]."""
    if not when:
        return False
    if isinstance(when, (int, float)):
        d = dt.datetime.fromtimestamp(when).date()
    else:
        try:
            d = dt.date.fromisoformat(str(when)[:10])
        except ValueError:
            return False
    return start <= d <= end


def health_part(db, start, end):
    tbl = H.daily(db, DAYS * 2, end)          # 14 дней, кончая вчера, свежие сверху
    this, prev = tbl[:DAYS], tbl[DAYS:DAYS * 2]
    out = ["## Тело за неделю (сон засчитан на утро; «без часов» — часы не носились, это пропуск, а не событие)",
           "дата       сон    глубокий  RHR  HRV   шаги   тренировки"]
    for d in this:
        s = d["sleep"]
        w = "; ".join(f"{x['ru']} {x['min']} мин" + (f" {x['km']} км" if x['km'] else "") for x in d["workouts"]) or "—"
        out.append(f"{d['date'][5:]} {d['dow']}  {hm(s['total']) if s else '—':6} {hm(s['deep']) if s else '—':8}  "
                   f"{d['rhr'] or '—':>3}  {d['hrv'] or '—':>3}  {d['steps'] if d['steps'] is not None else '—':>6}  {w}"
                   + ("   ← без часов" if d["noWatch"] else ""))

    def avgs(t):
        worn = [d for d in t if not d["noWatch"]]
        return {"sleep": H.avg([d["sleep"]["total"] for d in worn if d["sleep"]]),
                "deep": H.avg([d["sleep"]["deep"] for d in worn if d["sleep"]]),
                "rhr": H.avg([d["rhr"] for d in worn]), "hrv": H.avg([d["hrv"] for d in worn]),
                "steps": H.avg([d["steps"] for d in t]), "workouts": sum(len(d["workouts"]) for d in t),
                "minutes": sum(w["min"] for d in t for w in d["workouts"])}
    a, b = avgs(this), avgs(prev)
    out.append("")
    out.append(f"Эта неделя: сон {hm(a['sleep'])}, глубокий {hm(a['deep'])}, RHR {a['rhr'] or '—'}, HRV {a['hrv'] or '—'}, "
               f"шаги {a['steps'] or '—'}, тренировок {a['workouts']} ({a['minutes']} мин)")
    out.append(f"Прошлая неделя: сон {hm(b['sleep'])}, глубокий {hm(b['deep'])}, RHR {b['rhr'] or '—'}, HRV {b['hrv'] or '—'}, "
               f"шаги {b['steps'] or '—'}, тренировок {b['workouts']} ({b['minutes']} мин)")
    by = collections.Counter()
    mins = collections.Counter()
    for d in this:
        for w in d["workouts"]:
            by[w["ru"]] += 1; mins[w["ru"]] += w["min"]
    if by:
        out.append("По дисциплинам за неделю: " + ", ".join(f"{k} ×{n} ({mins[k]} мин)" for k, n in by.most_common()))
    # Когда каждая дисциплина из целей была в последний раз — за 90 дней, чтобы «плавания нет с августа» было цифрой.
    last = {}
    for d in H.daily(db, 90, end):
        for w in d["workouts"]:
            last.setdefault(w["ru"], d["date"])
    out.append("Последний раз: " + ", ".join(f"{k} — {last[k][5:] if k in last else 'не было за 90 дней'}" for k in GOAL_KINDS))
    return out


def media_part(start, end):
    out = ["\n## Что слушал, читал и смотрел"]
    lec = L.load()
    done = [l for l in lec["lectures"] if l.get("status") == "listened" and _in(l.get("listenedAt"), start, end)]
    now = [l for l in lec["lectures"] if l.get("status") == "listening"]
    if done:
        out.append("Лекции дослушал: " + "; ".join(f"{l['title']} ({H.hm((l.get('duration') or 0) / 60)})" for l in done))
    if now:
        out.append("Лекции в процессе: " + "; ".join(
            f"{l['title']} — {round((l.get('position') or 0) / max(l.get('duration') or 1, 1) * 100)}%" for l in now))
    played = [m for m in X.load() if _in(m.get("playedAt"), start, end)]
    if played:
        out.append("Миксы включал: " + "; ".join(
            f"{m.get('artist') or '?'} — {m.get('title')}"
            + (f" (до {H.hm((m.get('position') or 0) / 60)} из {H.hm((m.get('duration') or 0) / 60)})" if m.get("position") else "")
            for m in sorted(played, key=lambda m: -(m.get("playedAt") or 0))[:10]))
    books = B.load()
    fin = [b for b in books if b.get("status") == "read" and _in(b.get("finishedAt"), start, end)]
    reading = [b for b in books if b.get("status") == "reading"]
    if fin:
        out.append("Книги дочитал: " + "; ".join(f"{b['title']} — {b.get('author') or '?'}" for b in fin))
    if reading:
        out.append("Читаю: " + "; ".join(f"{b['title']} — {b.get('author') or '?'}" for b in reading))
    films = [m for m in M.load() if m.get("status") == "watched"
             and (_in(m.get("watchedAt"), start, end) or (not m.get("watchedAt") and _in(m.get("addedAt"), start, end)))]
    if films:
        out.append("Фильмы посмотрел: " + "; ".join(f"{m['title']}" + (f" — {m['rating']}/10" if m.get("rating") else "") for m in films))
    rd = _load("reads.json")
    took = [h for h in rd.get("history", []) if h.get("verdict") in ("saved", "read") and _in(h.get("at"), start, end)]
    if took:
        out.append("Статьи: " + "; ".join(f"{h.get('title')} ({'прочитал' if h.get('verdict') == 'read' else 'отложил'})" for h in took))
    fr = _load("french.json")
    ans = [a for a in fr.get("answers", []) if _in(a.get("at"), start, end)]
    if ans:
        ok = sum(1 for a in ans if a.get("correct"))
        out.append(f"Французский: {len(ans)} ответов, верных {ok}; слов к повторению {len(fr.get('cards', []))}")
    else:
        out.append("Французский: за неделю ни одного ответа")
    # Вердикты по советам — по всем видам разом: это и есть обратная связь, ради которой советы существуют.
    v = collections.Counter()
    t = _load("taste_recs.json")
    for kind in ("film", "book", "lecture"):
        for h in (t.get(kind) or {}).get("history", []):
            if _in(h.get("at"), start, end) and h.get("verdict") in ("liked", "dismissed", "listened"):
                v[f"{kind}:{h['verdict']}"] += 1
    for h in _load("mix_recs.json").get("history", []):
        if _in(h.get("at"), start, end) and h.get("verdict") in ("liked", "dismissed"):
            v[f"mix:{h['verdict']}"] += 1
    for h in rd.get("history", []):
        if _in(h.get("at"), start, end) and h.get("verdict") in ("saved", "dismissed", "read"):
            v[f"read:{h['verdict']}"] += 1
    if v:
        out.append("Ответы на советы Claude: " + ", ".join(f"{k} {n}" for k, n in sorted(v.items())))
    else:
        out.append("Ответов на советы Claude за неделю не было")
    return out if len(out) > 1 else []


def spend_part(start, end):
    try:
        import spend as SP
        rs = SP.rows(since=(start - dt.timedelta(days=DAYS)).isoformat())
    except Exception:
        return []
    if not rs:
        return []
    this = [r for r in rs if start.isoformat() <= r["date"] <= end.isoformat()]
    prev = [r for r in rs if r["date"] < start.isoformat()]
    if not this:
        return ["\n## Деньги: чеков за неделю нет"]
    tot = lambda xs: round(sum(r["sum"] for r in xs))  # noqa: E731
    titles = SP.rules().get("titles", {})
    cats = collections.Counter()
    for r in this:
        if not r["big"]:
            cats[titles.get(r["category"], r["category"])] += r["sum"]
    out = ["\n## Деньги за неделю (по чекам из налоговой; крупное отдельно)",
           f"Всего {tot([r for r in this if not r['big']]):,} ₽ против {tot([r for r in prev if not r['big']]):,} ₽ неделей раньше".replace(",", " ")]
    out.append("По категориям: " + ", ".join(f"{k} {round(v):,}".replace(",", " ") + " ₽" for k, v in cats.most_common(6)))
    taxi = [r for r in this if r["merchant"] == "Яндекс.Такси"]
    deliv = [r for r in this if r["category"] == "доставка-еды"]
    bits = []
    if taxi:
        bits.append(f"такси {len(taxi)} раз на {tot(taxi):,} ₽".replace(",", " "))
    if deliv:
        bits.append(f"доставка еды {len(deliv)} раз на {tot(deliv):,} ₽".replace(",", " "))
    if bits:
        out.append("; ".join(bits))
    big = [r for r in this if r["big"]]
    if big:
        out.append("Крупное: " + "; ".join(f"{r['merchant']} {round(r['sum']):,} ₽".replace(",", " ") for r in big))
    return out


def calendar_part(today):
    try:
        ag = S.agenda(days=DAYS, start_date=today, skip=S.hidden_calendars())
    except Exception:
        return []
    out = ["\n## Впереди по календарю (рабочий блок пропущен)"]
    n = 0
    for day, evs in ag.items():
        evs = [e for e in evs if (e["summary"] or "").strip().lower() not in ROUTINE]
        if not evs:
            continue
        n += len(evs)
        out.append(f"- {S.DOW[day.weekday()]} {day:%d.%m}: " + "; ".join(
            ("весь день" if e["allday"] else f"{e['start']:%H:%M}") + f" {e['summary']}" + (f" ({e['who']})" if e.get("who") else "")
            for e in evs))
    return out if n else ["\n## Впереди по календарю: кроме работы ничего не стоит"]


def nights_part(db):
    try:
        b = NI.brief(60, db)
    except Exception as e:
        print(f"week: nights: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    if not b:
        return []
    return ["\n## Ночи против вечеров (медианы за 60 дней; это арифметика, не вывод — но повод присмотреться)", b]


def digest(db=None, today=None):
    db = db or H.connect()
    today = today or dt.datetime.now(H.tz()).date()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=DAYS - 1)
    out = [f"Неделя {start:%d.%m}–{end:%d.%m.%Y}, сегодня {S.DOW[today.weekday()]} {today:%d.%m}"]
    out += health_part(db, start, end)
    out += media_part(start, end)
    out += spend_part(start, end)
    out += calendar_part(today)
    out += nights_part(db)
    prev = H.last_review(db, "week")
    if prev:
        out.append(f"\n## Прошлые итоги ({H.fmt_local(prev['ts'])[:5]}) — не повторяй их, продолжай")
        out.append(prev["text"].strip())
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] not in ("digest",):
        sys.exit(__doc__)
    print(digest())
