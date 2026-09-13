#!/usr/bin/env python3
"""Советы по фильмам и книгам. Claude Code (Opus) смотрит на то, что я оценил, что бросил
и какие вердикты я вынес его прошлым советам, предлагает новое — а мы проверяем, что оно
существует, через OMDb (фильмы) и Google Books / Open Library (книги). Данные — taste_recs.json.

Usage:
  taste_recs.py digest film|book              # контекст вкуса, который видит модель
  taste_recs.py due film|book                 # код 0, если пора обновлять (для taste_recs.sh)
  taste_recs.py apply film|book --file answer.json [--model opus] [--keep 3]
  taste_recs.py verdict film|book <id> liked|dismissed
  taste_recs.py list [film|book]

Механизм повторяет mix_recs.py, разница только в способе проверки кандидата и в том,
куда уезжает принятый совет: фильм — в movies.json со статусом to-watch, книга — в books.json
со статусом to-read. Запускается раз в день на mini (scripts/taste_recs.sh из launchd,
deploy/install-taste-recs.sh) и по кнопке на странице (POST /api/recs/<kind>/refresh).
"""
import argparse, datetime, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "taste_recs.json")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import movies as M  # noqa: E402
import books as B  # noqa: E402

KINDS = ("film", "book")
VERDICTS = ("liked", "dismissed")
HISTORY_MAX = 120
EVERY_HOURS = 20  # суточная задача, которая может запуститься поздно (mini выключали), не должна отказываться


def today():
    return datetime.date.today().isoformat()


def load():
    if not os.path.exists(DATA):
        db = {}
    else:
        with open(DATA, encoding="utf-8") as f:
            db = json.load(f)
    for k in KINDS:
        d = db.setdefault(k, {})
        d.setdefault("updatedAt", None)
        d.setdefault("model", None)
        d.setdefault("items", [])
        d.setdefault("history", [])
    return db


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


def check_kind(kind):
    if kind not in KINDS:
        raise SystemExit(f"bad kind: {kind} (film|book)")
    return kind


# ---------------------------------------------------------------- digest
def _film_digest(d):
    movies = M.load()
    out = []
    rated = sorted([m for m in movies if m.get("rating")], key=lambda m: -m["rating"])
    out.append(f"## Что я смотрел и как оценил ({len(rated)} фильмов, шкала 1–10)")
    for m in rated:
        bits = [str(m["year"])] if m.get("year") else []
        if m.get("director"):
            bits.append(m["director"])
        if m.get("genre"):
            bits.append(", ".join(m["genre"][:3]))
        line = f"- {m['rating']}: {m['title']} ({'; '.join(bits)})"
        if m.get("note"):
            line += f" — моя заметка: {m['note']}"
        out.append(line)

    plans = [m for m in movies if m.get("status") in ("to-watch", "watching")]
    out.append(f"\n## Уже в списке «посмотреть» — не предлагай это ({len(plans)})")
    out += [f"- {m['title']} ({m.get('year') or '?'})" for m in plans] or ["(пусто)"]
    return out


def _book_digest(d):
    """У книг нет оценок (`rating` везде пустой, теги — цветные квадраты из Obsidian), поэтому
    вкус здесь читается по составу: что прочитано, что брошено, что лежит в планах. Если оценки
    и комментарии когда-нибудь появятся (страница умеет их править), они попадут в дайджест сами."""
    books = B.load()
    out = []

    def line(b):
        s = f"- {b['title']} — {b.get('author') or '?'}"
        if b.get("year"):
            s += f" ({b['year']})"
        if b.get("rating"):
            s += f" · моя оценка {b['rating']}/10"
        if b.get("comment"):
            s += f" — {b['comment']}"
        return s

    read = [b for b in books if b.get("status") == "read"]
    rated = [b for b in read if b.get("rating")]
    read.sort(key=lambda b: (-(b.get("rating") or 0), b.get("title") or ""))
    out.append(f"## Что я прочитал ({len(read)})"
               + (f", из них {len(rated)} с моей оценкой по шкале 1–10" if rated
                  else " — оценок я не ставил, сигнал тут сам факт прочтения"))
    out += [line(b) for b in read] or ["(пусто)"]

    dropped = [b for b in books if b.get("status") == "abandoned"]
    if dropped:
        out.append(f"\n## Книги, которые я бросил — прямой сигнал, чего не предлагать ({len(dropped)})")
        out += [line(b) for b in dropped]

    plans = [b for b in books if b.get("status") == "to-read"]
    out.append(f"\n## Уже в планах — не предлагай это ({len(plans)})")
    out += [f"- {b['title']} — {b.get('author') or '?'}" for b in plans] or ["(пусто)"]
    return out


def digest(kind):
    d = load()[check_kind(kind)]
    out = _film_digest(d) if kind == "film" else _book_digest(d)

    if d["items"]:
        out.append("\n## Твои советы, которые сейчас висят у меня на странице (не повторяй их)")
        for r in d["items"]:
            out.append(f"- {r.get('title')}" + (f" — {r.get('author')}" if r.get("author") else ""))
    if d["history"]:
        out.append("\n## Вердикты по прошлым советам (свежие сверху)")
        word = {"liked": "ВЗЯЛ", "dismissed": "НЕ ТО", "new": "без ответа"}
        for h in d["history"][:40]:
            out.append(f"- {h.get('at')} · {word.get(h.get('verdict'), h.get('verdict'))}: "
                       f"{h.get('title')}" + (f" — {h.get('author')}" if h.get("author") else "")
                       + f" — {h.get('reason') or ''}")
    else:
        out.append("\n## Вердикты по прошлым советам\n(это первый подбор, вердиктов ещё нет)")
    return "\n".join(out)


# ---------------------------------------------------------------- разбор ответа модели
def parse_answer(text):
    """Модель просят отдать голый JSON-массив; прощаем ```-заборы и болтовню вокруг."""
    text = re.sub(r"```[a-z]*", "", text)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < i:
        raise SystemExit("no JSON array in the model answer")
    data = json.loads(text[i:j + 1])
    if not isinstance(data, list):
        raise SystemExit("model answer is not a list")
    return [c for c in data if isinstance(c, dict) and c.get("title")]


def known_ids(kind, d):
    ids = {r["id"] for r in d["items"]} | {h["id"] for h in d["history"]}
    if kind == "film":
        ids |= {m["id"] for m in M.load()}
    else:
        ids |= {b["id"] for b in B.load()}
    return ids


def resolve_film(c, exclude):
    """Совет модели → реальная запись OMDb. Без этого в списке оказались бы выдуманные фильмы."""
    try:
        imdb_id = M.resolve(c["title"], c.get("year"))
    except SystemExit as e:
        print(f"  не нашёл в OMDb: {c['title']} ({e})")
        return None
    if imdb_id in exclude:
        print(f"  уже известен: {c['title']}")
        return None
    try:
        m = M.metadata(imdb_id)
    except SystemExit as e:
        print(f"  нет метаданных: {c['title']} ({e})")
        return None
    # Обложку качаем сразу: внешние ссылки (m.media-amazon.com) с домашнего интернета не открываются,
    # ровно поэтому постеры каталога и лежат локально. coverUrl остаётся запасным вариантом.
    return {"id": m["id"], "title": m["title"], "year": m["year"], "author": m.get("director"),
            "cover": M.fetch_poster(m["id"], m.get("posterUrl")) or m.get("posterUrl"),
            "coverUrl": m.get("posterUrl"), "meta": ", ".join(filter(None, [
                ", ".join(m.get("genre") or []),
                f"{m['runtime']} мин" if m.get("runtime") else None,
                f"IMDb {m['imdbRating']}" if m.get("imdbRating") else None])),
            "runtime": m.get("runtime"), "plot": m.get("plot")}


def resolve_book(c, exclude):
    # Строгий запрос Google Books (intitle/inauthor) часто промахивается по русским изданиям,
    # поэтому вторым заходом ищем просто «название автор» без фильтров.
    res = B.lookup(c["title"], c.get("author"), None, 1)
    if not res and c.get("author"):
        res = B.lookup(f"{c['title']} {c['author']}", None, None, 1)
    if not res:
        # С mini оба каталога недоступны (Google Books — 429 с адреса VPN, Open Library не отвечает),
        # и это не повод терять совет: оставляем его непроверенным, страница честно это показывает.
        bid = B.slug(f"{c['title']}-{c.get('author') or ''}")
        if bid in exclude:
            return None
        print(f"  не подтверждено в каталогах, оставляю как есть: {c['title']}")
        return {"id": bid, "title": c["title"], "year": None, "author": c.get("author"),
                "cover": None, "coverUrl": None, "isbn": None, "unverified": True,
                "meta": c.get("author") or "", "plot": None}
    r = res[0]
    bid = r["isbn"] or B.slug(f"{r['title']}-{r['author']}")
    if bid in exclude:
        print(f"  уже известна: {c['title']}")
        return None
    return {"id": bid, "title": r["title"], "year": r.get("year"), "author": r.get("author"),
            "cover": B.fetch_cover(bid, r.get("coverUrl")) or r.get("coverUrl"),
            "coverUrl": r.get("coverUrl"), "isbn": r.get("isbn"),
            "meta": ", ".join(filter(None, [r.get("author"), str(r["year"]) if r.get("year") else None,
                                            f"{r['pages']} с." if r.get("pages") else None])),
            "plot": (r.get("description") or "")[:400] or None}


def apply_answer(kind, text, model=None, keep=3):
    check_kind(kind)
    db = load()
    d = db[kind]
    exclude = known_ids(kind, d)
    items = []
    for c in parse_answer(text):
        if len(items) >= keep:
            break
        if items and kind == "book":
            time.sleep(2)  # Google Books считает частоту запросов, а не только их число
        r = resolve_film(c, exclude) if kind == "film" else resolve_book(c, exclude)
        if not r:
            continue
        exclude.add(r["id"])
        r.update(reason=(c.get("why") or "").strip() or None, suggestedAt=today(), verdict="new")
        items.append(r)
        print(f"  + {r['title']}" + (f" — {r['author']}" if r.get("author") else ""))
    if not items:
        raise SystemExit("ни один совет не подтвердился")
    # то, что осталось без ответа, уезжает в историю как «без ответа»: новая пачка его заменяет
    stale = [r for r in d["items"] if r["id"] not in {i["id"] for i in items}]
    d["history"] = [history_entry(r) for r in stale] + d["history"]
    d["history"] = d["history"][:HISTORY_MAX]
    d["items"] = items
    d["updatedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    d["model"] = model or d.get("model")
    save(db)
    return items


def history_entry(r):
    return {"id": r["id"], "title": r.get("title"), "author": r.get("author"), "year": r.get("year"),
            "reason": r.get("reason"), "verdict": r.get("verdict") or "new",
            "at": r.get("decidedAt") or r.get("suggestedAt") or today()}


# ---------------------------------------------------------------- вердикты
def verdict(kind, rid, v):
    check_kind(kind)
    if v not in VERDICTS:
        raise SystemExit(f"bad verdict: {v}")
    db = load()
    d = db[kind]
    r = next((x for x in d["items"] if x["id"] == rid), None)
    if not r:
        raise SystemExit(f"no such suggestion: {rid}")
    r["verdict"] = v
    r["decidedAt"] = today()
    added = None
    if v == "liked":
        added = _add_to_catalog(kind, r)
    if not added:
        _drop_cover(r)
    d["items"] = [x for x in d["items"] if x["id"] != rid]
    d["history"] = ([history_entry(r)] + d["history"])[:HISTORY_MAX]
    save(db)
    return {"rec": r, "added": added}


def _drop_cover(r):
    """Отвергнутый совет не должен оставлять картинку в posters/ или covers/."""
    p = str(r.get("cover") or "")
    if p.startswith(("posters/", "covers/")):
        try:
            os.remove(os.path.join(ROOT, p))
        except OSError:
            pass


def _add_to_catalog(kind, r):
    """Принятый совет уезжает в каталог как «хочу посмотреть/прочитать» — с локальной обложкой."""
    if kind == "film":
        movies = M.load()
        if any(m["id"] == r["id"] for m in movies):
            return None
        m = M.metadata(r["id"])
        m["poster"] = M.fetch_poster(m["id"], m.get("posterUrl"))
        m.update(rating=None, status="to-watch", addedAt=today(), watchedAt=None,
                 note=r.get("reason"))
        movies.append(m)
        M.save(movies)
        return {"id": m["id"], "title": m["title"], "status": m["status"]}

    books = B.load()
    if any(b["id"] == r["id"] for b in books):
        return None
    b = {"id": r["id"], "title": r["title"], "titleAlt": None, "author": r.get("author"),
         "year": r.get("year"), "pages": None, "isbn": r.get("isbn"),
         "description": r.get("plot"), "coverUrl": r.get("coverUrl") or r.get("cover"),
         "cover": (r.get("cover") if str(r.get("cover") or "").startswith("covers/")
                   else B.fetch_cover(r["id"], r.get("coverUrl") or r.get("cover"))), "status": "to-read", "rating": None,
         "comment": r.get("reason"), "addedAt": today(), "finishedAt": None, "tags": ["claude-rec"]}
    books.append(b)
    B.save(books)
    return {"id": b["id"], "title": b["title"], "status": b["status"]}


def due(kind):
    d = load()[check_kind(kind)]
    if not d["items"]:
        return True, "советов ещё нет"
    if not d.get("updatedAt"):
        return True, "ни разу не запускался"
    try:
        last = datetime.datetime.fromisoformat(d["updatedAt"])
    except ValueError:
        return True, "битый updatedAt"
    hours = (datetime.datetime.now() - last).total_seconds() / 3600
    if hours >= EVERY_HOURS:
        return True, f"прошло {hours:.1f} ч"
    return False, f"прошло {hours:.1f} ч, следующий через {EVERY_HOURS - hours:.1f} ч"


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("digest", "due"):
        p = sub.add_parser(name)
        p.add_argument("kind", choices=KINDS)
    a = sub.add_parser("apply")
    a.add_argument("kind", choices=KINDS)
    a.add_argument("--file", required=True, help="ответ модели (JSON-массив), - для stdin")
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=3)
    v = sub.add_parser("verdict")
    v.add_argument("kind", choices=KINDS)
    v.add_argument("id")
    v.add_argument("verdict", choices=VERDICTS)
    ls = sub.add_parser("list")
    ls.add_argument("kind", nargs="?", choices=KINDS)
    args = ap.parse_args()

    if args.cmd == "digest":
        print(digest(args.kind))
    elif args.cmd == "due":
        ok, why = due(args.kind)
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "apply":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        items = apply_answer(args.kind, text, args.model, args.keep)
        print(f"{len(items)} советов сохранено в taste_recs.json ({args.kind})")
    elif args.cmd == "verdict":
        out = verdict(args.kind, args.id, args.verdict)
        print(f"{args.id}: {args.verdict}" + (" (уехало в каталог)" if out["added"] else ""))
    elif args.cmd == "list":
        db = load()
        for kind in ([args.kind] if args.kind else KINDS):
            d = db[kind]
            print(f"[{kind}] обновлено {d.get('updatedAt')} ({d.get('model')})")
            for r in d["items"]:
                print(f"  {r['id']}  {r['title']}" + (f" — {r['author']}" if r.get("author") else ""))
                if r.get("reason"):
                    print(f"      {r['reason']}")
            if d["history"]:
                print(f"  история: {len(d['history'])} записей, "
                      f"взято {sum(1 for h in d['history'] if h['verdict'] == 'liked')}")


if __name__ == "__main__":
    main()
