#!/usr/bin/env python3
"""Контекст обо мне для всех промптов сайта — одно место вместо пяти (29.09.2026).

До этого каждый промпт собирал меня сам: полный вкус жил в `reads.taste()` (его же брал французский),
компактный — в `taste_recs._cross()` с другими порогами («не зашло» ≤ 5 против ≤ 6), тело — в
`health.digest`, день — в `schedule.digest`, нагрузка — в `load.digest`, всё это склеивал shell. Ни одна
сборка не знала, что сейчас в работе: какую книгу я читаю, что начал смотреть, под какую книгу собрана
серия лекций, что забрал из советов за последний месяц. Здесь всё это — именованные секции:

  taste       полный вкус по четырём каталогам (статьи, французский)
  cross       тот же вкус одной строкой на список, без своего каталога (советы по фильмам, книгам, лекциям)
  now         что сейчас в работе и что недавно взято из советов — во все советы
  goals       health/goals.md
  body        health.digest (сон, пульс, HRV, шаги, тренировки, ночь и форма из vitals.py)
  day         schedule.digest
  load        load.digest
  health      всё, что уходит в часовую заметку, после PROMPT.md — это и печатает health_review.sh

  context.py show [секция …]     секции с размером — «что модель знает обо мне»
  context.py <секция> [арг]      одна секция как есть (cross film|book|lecture)
  context.py health              хвост промпта часовой заметки
"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

TOP = 7.5          # оценка фильма, от которой он — направление вкуса
LOW = 6            # и до которой — «не зашло»
TOP_FULL = 25      # сколько любимых фильмов в полном вкусе
RECENT_DAYS = 30   # окно «недавно взял из советов»


def _json(name, default):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _age(when):
    try:
        return (dt.date.today() - dt.date.fromisoformat(str(when)[:10])).days
    except ValueError:
        return 10 ** 6


def _book(b):
    return f"{b['title']} — {b.get('author') or '?'}"


# ---------------------------------------------------------------- вкус
def taste():
    """Вкус по всем четырём каталогам сразу. Бывший `reads.taste()`, слово в слово, кроме общего порога LOW."""
    import movies as M, books as B, lectures as L, mixes as X, mix_recs as R
    out = []
    rated = sorted([m for m in M.load() if m.get("rating")], key=lambda m: -m["rating"])
    out.append("## Фильмы, которые я любил больше всего (оценка из 10)")
    for m in rated[:TOP_FULL]:
        bits = [str(m.get("year") or ""), m.get("director") or "", ", ".join((m.get("genre") or [])[:2])]
        out.append(f"- {m['rating']}: {m['title']} ({'; '.join(b for b in bits if b)})"
                   + (f" — {m['note']}" if m.get("note") else ""))
    low = [m for m in rated if m["rating"] <= LOW][-8:]
    if low:
        out.append("\n## Фильмы, которые мне не зашли")
        out += [f"- {m['rating']}: {m['title']}" for m in low]

    books = B.load()
    read = [b for b in books if b.get("status") == "read"]
    out.append(f"\n## Книги, которые я прочитал ({len(read)})")
    out += [f"- {_book(b)}" for b in read]
    dropped = [b for b in books if b.get("status") == "abandoned"]
    if dropped:
        out.append("\n## Книги, которые я бросил (сильный сигнал)")
        out += [f"- {_book(b)}" for b in dropped]
    plans = [b for b in books if b.get("status") == "to-read"]
    if plans:
        out.append("\n## Книги в планах")
        out += [f"- {_book(b)}" for b in plans[:25]]

    active = [l for l in L.load()["lectures"] if l["status"] in ("listened", "listening", "queued")]
    if active:
        out.append("\n## Лекции, которые я слушал или поставил в планы")
        out += [f"- {l['title']}" for l in active]

    mixes = X.load()
    if mixes:
        out.append(f"\n## Музыка, которую я слушаю ({len(mixes)} миксов и радиошоу)")
        out += [f"- {m.get('artist') or '?'} — {m.get('title')}"
                + (f" [{m['genre']}]" if m.get("genre") else "") for m in mixes[:25]]
    liked = [h for h in R.load().get("history", []) if h.get("verdict") == "liked"][:10]
    if liked:
        out.append("\n## Из советов по музыке я забрал себе")
        out += [f"- {h.get('artist')} — {h.get('title')}" for h in liked]
    return out


def cross(kind):
    """Тот же вкус компактно, без своего каталога: названия, без причин — подробности есть в своём разделе."""
    import movies as M, books as B, lectures as L
    out = ["\n## Другие каталоги — тот же вкус с другой стороны"]
    if kind != "film":
        movies = M.load()
        top = sorted([m for m in movies if (m.get("rating") or 0) >= TOP], key=lambda m: -m["rating"])
        if top:
            out.append(f"Фильмы, которые я оценил на {TOP} и выше ({len(top)}): " + "; ".join(
                f"{m['title']}" + (f" ({m['director']})" if m.get("director") else "") for m in top))
        low = [m for m in movies if m.get("rating") and m["rating"] <= LOW]
        if low:
            out.append("Фильмы, которые не зашли: " + "; ".join(m["title"] for m in low))
    if kind != "book":
        books = B.load()
        read = [b for b in books if b.get("status") == "read"]
        if read:
            out.append(f"Книги, которые я прочитал ({len(read)}): " + "; ".join(_book(b) for b in read))
        dropped = [b for b in books if b.get("status") == "abandoned"]
        if dropped:
            out.append("Книги, которые бросил: " + "; ".join(_book(b) for b in dropped))
    if kind != "lecture":
        done = [l for l in L.load()["lectures"] if l.get("status") in ("listened", "listening")]
        if done:
            out.append(f"Лекции, которые я дослушал или слушаю ({len(done)}): " + "; ".join(l["title"] for l in done))
    return out if len(out) > 1 else []


def now():
    """Что сейчас в работе. Каталоги говорят «что было» и «что в планах», но не «чем я живу сейчас»: книга,
    под которую собрана серия лекций, важнее для совета, чем двадцатая строка списка прочитанного."""
    import movies as M, books as B, lectures as L, mixes as X
    out = []
    reading = [b for b in B.load() if b.get("status") == "reading"]
    if reading:
        out.append("Читаю: " + "; ".join(_book(b) for b in reading))
    watching = [m for m in M.load() if m.get("status") == "watching"]
    if watching:
        out.append("Смотрю: " + "; ".join(f"{m['title']} ({m.get('year') or '?'})" for m in watching))
    lec = L.load()
    series = lec.get("series") or {}
    listening = [l for l in lec["lectures"] if l.get("status") == "listening"]
    listening.sort(key=lambda l: l.get("touchedAt") or "", reverse=True)
    if listening:
        out.append("Слушаю лекции: " + "; ".join(
            f"{l['title']} ({round((l.get('position') or 0) / max(l.get('duration') or 1, 1) * 100)}%)" for l in listening[:4]))
    reserve = [series.get(s) for s in lec.get("reserve") or [] if series.get(s)]
    if reserve:
        out.append("Серия лекций к тому, что читаю: " + "; ".join(reserve))
    week_ago = (dt.datetime.now() - dt.timedelta(days=7)).timestamp()
    played = sorted([m for m in X.load() if (m.get("playedAt") or 0) >= week_ago], key=lambda m: -m["playedAt"])
    if played:
        out.append("Миксы за неделю: " + "; ".join(f"{m.get('artist') or '?'} — {m.get('title')}" for m in played[:6]))
    fr = _json("french.json", {})
    if fr.get("level"):
        out.append(f"Учу французский, уровень {fr['level']}, с преподавателем по вторникам и четвергам")
    rd = _json("reads.json", {})
    if rd.get("saved"):
        out.append("Статьи, отложенные прочитать: " + "; ".join(f"{a.get('title')} ({a.get('source')})" for a in rd["saved"][:6]))

    # Взятое из советов за месяц — по всем видам сразу: книга, взятая по совету, говорит и лекциям, и статьям.
    took = []
    tr = _json("taste_recs.json", {})
    word = {"film": "фильм", "book": "книгу", "lecture": "лекцию"}
    for kind, name in word.items():
        for h in (tr.get(kind) or {}).get("history", []):
            if h.get("verdict") in ("liked", "listened") and _age(h.get("at")) <= RECENT_DAYS:
                took.append((h.get("at") or "", f"{name} «{h.get('title')}»"))
    for h in _json("mix_recs.json", {}).get("history", []):
        if h.get("verdict") == "liked" and _age(h.get("at")) <= RECENT_DAYS:
            took.append((h.get("at") or "", f"микс {h.get('artist')} — {h.get('title')}"))
    for h in rd.get("history", []):
        if h.get("verdict") in ("saved", "read") and _age(h.get("at")) <= RECENT_DAYS:
            took.append((h.get("at") or "", f"статью «{h.get('title')}»"))
    if took:
        took.sort(reverse=True)
        seen = []                      # статья «отложил», а потом «прочитал» — это одно взятое, не два
        for _, t in took:
            if t not in seen:
                seen.append(t)
        out.append(f"Из советов за {RECENT_DAYS} дней взял: " + "; ".join(seen[:12]))
    return (["\n## Сейчас"] + out) if out else []


# ---------------------------------------------------------------- тело и день
def goals():
    try:
        with open(os.path.join(ROOT, "health", "goals.md"), encoding="utf-8") as f:
            return [l.rstrip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return []


def body(days=7):
    import health as H
    return H.digest(days=days).splitlines()


def day():
    import schedule as S
    try:
        return S.digest().splitlines()
    except Exception:
        return []   # нет сети, сломался календарь — заметка пишется без расписания, как раньше


def load_():
    import load as LD
    try:
        return LD.digest().splitlines()
    except Exception:
        return []


def health():
    """Всё, что идёт в часовую заметку после PROMPT.md. Секция без данных просто выпадает."""
    out = ["", "## Мои цели"] + (goals() or ["(целей пока нет — ориентируйся на мои средние и общие нормы)"])
    d = day()
    if d:
        out += ["", "## Мой день"] + d
    ld = load_()
    if ld:
        out += ["", "## Нагрузка и старты"] + ld
    out += ["", "## Данные"] + body()
    return out


SECTIONS = {"taste": taste, "cross": cross, "now": now, "goals": goals, "body": body, "day": day,
            "load": load_, "health": health}


def main():
    args = sys.argv[1:] or ["show"]
    if args[0] == "show":
        names = args[1:] or ["now", "goals", "day", "load", "body", "taste"]
        for n in names:
            lines = SECTIONS[n]("film") if n == "cross" else SECTIONS[n]()
            text = "\n".join(lines)
            print(f"==== {n}: {len(lines)} строк, {len(text)} знаков (~{len(text) // 3} токенов)")
            print(text.strip() or "(пусто)")
            print()
        return
    fn = SECTIONS.get(args[0])
    if not fn:
        sys.exit("секции: " + ", ".join(SECTIONS))
    print("\n".join(fn(args[1]) if args[0] == "cross" else fn()))


if __name__ == "__main__":
    main()
