#!/usr/bin/env python3
"""Советы по статьям. Кандидаты берутся из настоящих RSS-лент, а Claude только выбирает из них
три под мой вкус по всем четырём каталогам сразу (фильмы, книги, лекции, миксы). Данные — reads.json.

Usage:
  reads.py fetch [--days 45]        # скачать ленты в reads.json (cache)
  reads.py digest                   # то, что видит модель: вкус + кандидаты
  reads.py due                      # код 0, если пора обновлять (для reads.sh)
  reads.py apply --file answer.json [--model opus] [--keep 3]
  reads.py verdict <id> saved|dismissed|read
  reads.py list

Почему так: модель прекрасно сочиняет правдоподобные ссылки на несуществующие статьи — ровно как
выдумывала фильмы, пока их не начали проверять в OMDb. Здесь она вообще не называет URL: выбирает
номер из списка, который скрипт сам скачал из ленты, поэтому ссылка всегда живая.

Запускается раз в день на mini (scripts/reads.sh из launchd, deploy/install-reads.sh)
и по кнопке на странице (POST /api/reads/refresh).
"""
import argparse, datetime, email.utils, hashlib, html, json, os, re, sys, urllib.error, urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "reads.json")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import movies as M  # noqa: E402
import books as B  # noqa: E402
import lectures as L  # noqa: E402
import mixes as X  # noqa: E402
import mix_recs as R  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
VERDICTS = ("saved", "dismissed", "read")
HISTORY_MAX = 300
CACHE_MAX = 400          # сколько кандидатов держим в reads.json
PER_SOURCE = 25          # свежих статей с одной ленты за раз
SUMMARY_CHARS = 280
EVERY_HOURS = 20

# Ленты проверены с mini (он ходит через VPN): все отдают 200 и настоящий XML.
SOURCES = [
    {"id": "lesswrong", "name": "LessWrong", "url": "https://www.lesswrong.com/feed.xml?view=curated-rss"},
    {"id": "acx", "name": "Astral Codex Ten", "url": "https://www.astralcodexten.com/feed"},
    {"id": "aeon", "name": "Aeon", "url": "https://aeon.co/feed.rss"},
    {"id": "psyche", "name": "Psyche", "url": "https://psyche.co/feed.rss"},
    {"id": "noema", "name": "Noema", "url": "https://www.noemamag.com/feed/"},
    {"id": "lrb", "name": "London Review of Books", "url": "https://www.lrb.co.uk/feeds/rss"},
    {"id": "parisreview", "name": "The Paris Review", "url": "https://www.theparisreview.org/blog/feed/"},
    {"id": "harpers", "name": "Harper's", "url": "https://harpers.org/feed/"},
    {"id": "newyorker", "name": "The New Yorker", "url": "https://www.newyorker.com/feed/magazine"},
    {"id": "quanta", "name": "Quanta", "url": "https://www.quantamagazine.org/feed/"},
    {"id": "nautilus", "name": "Nautilus", "url": "https://nautil.us/feed/"},
]


def today():
    return datetime.date.today().isoformat()


def load():
    if not os.path.exists(DATA):
        db = {}
    else:
        with open(DATA, encoding="utf-8") as f:
            db = json.load(f)
    db.setdefault("updatedAt", None)
    db.setdefault("model", None)
    db.setdefault("items", [])      # текущие советы
    db.setdefault("saved", [])      # «прочту» — список для чтения
    db.setdefault("history", [])    # вердикты
    db.setdefault("cache", [])      # кандидаты из лент
    db.setdefault("fetchedAt", None)
    return db


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- ленты
def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _tag(el):
    return el.tag.split("}")[-1]


def _first(el, names):
    for child in el:
        if _tag(child) in names and (child.text or "").strip():
            return child.text.strip()
    return None


def _link(el):
    for child in el:
        if _tag(child) != "link":
            continue
        if child.get("href") and child.get("rel", "alternate") == "alternate":
            return child.get("href")
        if (child.text or "").strip():
            return child.text.strip()
    return None


def _when(el):
    raw = _first(el, ("pubDate", "published", "updated", "date"))
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _author(el):
    a = _first(el, ("creator", "author"))
    if a:
        return a
    for child in el:
        if _tag(child) == "author":
            n = _first(child, ("name",))
            if n:
                return n
    return None


def parse_feed(xml_bytes, source):
    # У LRB лента начинается с пустых строк перед <?xml?>, и парсер на этом падает.
    root = ET.fromstring(xml_bytes.lstrip() if isinstance(xml_bytes, bytes) else xml_bytes.lstrip())
    out = []
    for el in root.iter():
        if _tag(el) not in ("item", "entry"):
            continue
        link = _link(el)
        title = _first(el, ("title",))
        if not link or not title:
            continue
        body = ""
        for child in el:
            if _tag(child) in ("encoded", "content", "description", "summary", "subtitle"):
                body = max(body, child.text or "", key=len)
        words = len(strip_html(body).split())
        out.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:10],
            "url": link,
            "title": strip_html(title),
            "author": _author(el),
            "source": source["name"],
            "sourceId": source["id"],
            "date": _when(el),
            # Слов в ленте столько, сколько отдал сайт: у ACX и LessWrong это весь текст,
            # у остальных — анонс. Поэтому время чтения показываем, только если текст пришёл целиком.
            "minutes": round(words / 220) if words > 400 else None,
            "summary": strip_html(body)[:SUMMARY_CHARS],
        })
    return out


def fetch(days=45, verbose=True):
    db = load()
    known = {c["id"] for c in db["cache"]}
    seen = {r["id"] for r in db["items"]} | {h["id"] for h in db["history"]} | {s["id"] for s in db["saved"]}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    fresh = []
    for src in SOURCES:
        try:
            req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                items = parse_feed(r.read(), src)
        except (urllib.error.URLError, ET.ParseError, OSError) as e:
            if verbose:
                print(f"  {src['name']}: не прочитал ленту ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        items = [i for i in items if not i["date"] or i["date"] >= cutoff][:PER_SOURCE]
        new = [i for i in items if i["id"] not in known and i["id"] not in seen]
        fresh += items
        if verbose:
            print(f"  {src['name']}: {len(items)} свежих, из них новых {len(new)}")
    by_id = {c["id"]: c for c in db["cache"]}
    for i in fresh:
        by_id[i["id"]] = i
    cache = [c for c in by_id.values() if c["id"] not in seen]
    cache.sort(key=lambda c: c.get("date") or "", reverse=True)
    db["cache"] = cache[:CACHE_MAX]
    db["fetchedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    save(db)
    return db["cache"]


# ---------------------------------------------------------------- дайджест
def taste():
    """Вкус по всем четырём каталогам сразу — ради этого всё и затевалось."""
    out = []
    movies = M.load()
    rated = sorted([m for m in movies if m.get("rating")], key=lambda m: -m["rating"])
    out.append("## Фильмы, которые я любил больше всего (оценка из 10)")
    for m in rated[:25]:
        bits = [str(m.get("year") or ""), m.get("director") or "", ", ".join((m.get("genre") or [])[:2])]
        out.append(f"- {m['rating']}: {m['title']} ({'; '.join(b for b in bits if b)})"
                   + (f" — {m['note']}" if m.get("note") else ""))
    low = [m for m in rated if m["rating"] <= 6][-8:]
    if low:
        out.append("\n## Фильмы, которые мне не зашли")
        out += [f"- {m['rating']}: {m['title']}" for m in low]

    books = B.load()
    read = [b for b in books if b.get("status") == "read"]
    out.append(f"\n## Книги, которые я прочитал ({len(read)})")
    out += [f"- {b['title']} — {b.get('author') or '?'}" for b in read]
    dropped = [b for b in books if b.get("status") == "abandoned"]
    if dropped:
        out.append("\n## Книги, которые я бросил (сильный сигнал)")
        out += [f"- {b['title']} — {b.get('author') or '?'}" for b in dropped]
    plans = [b for b in books if b.get("status") == "to-read"]
    if plans:
        out.append("\n## Книги в планах")
        out += [f"- {b['title']} — {b.get('author') or '?'}" for b in plans[:25]]

    lec = L.load()
    active = [l for l in lec["lectures"] if l["status"] in ("listened", "listening", "queued")]
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


def digest():
    db = load()
    out = taste()

    if db["history"]:
        out.append("\n## Вердикты по прошлым советам-статьям (свежие сверху)")
        word = {"saved": "ВЗЯЛ ЧИТАТЬ", "read": "прочитал", "dismissed": "МИМО", "new": "без ответа"}
        for h in db["history"][:40]:
            out.append(f"- {word.get(h.get('verdict'), h.get('verdict'))}: {h.get('title')} "
                       f"({h.get('source')}) — {h.get('reason') or ''}")
    else:
        out.append("\n## Вердикты по прошлым советам-статьям\n(это первый подбор)")

    out.append("\n## Кандидаты: свежие статьи из лент. Выбирай ТОЛЬКО из них, по номеру.")
    for n, c in enumerate(db["cache"], 1):
        head = f"{n}. [{c['source']}] {c['title']}"
        if c.get("author"):
            head += f" — {c['author']}"
        if c.get("minutes"):
            head += f" ({c['minutes']} мин чтения)"
        out.append(head)
        if c.get("summary"):
            out.append(f"   {c['summary']}")
    return "\n".join(out)


# ---------------------------------------------------------------- ответ модели
def parse_answer(text):
    text = re.sub(r"```[a-z]*", "", text)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < i:
        raise SystemExit("no JSON array in the model answer")
    data = json.loads(text[i:j + 1])
    if not isinstance(data, list):
        raise SystemExit("model answer is not a list")
    return [c for c in data if isinstance(c, dict) and c.get("n")]


def apply_answer(text, model=None, keep=3):
    db = load()
    cache = db["cache"]
    items = []
    for c in parse_answer(text):
        if len(items) >= keep:
            break
        try:
            n = int(c["n"])
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(cache):
            print(f"  номер вне списка: {c['n']}")
            continue
        art = dict(cache[n - 1])
        if any(i["id"] == art["id"] for i in items):
            continue
        art.update(reason=(c.get("why") or "").strip() or None, suggestedAt=today(), verdict="new")
        items.append(art)
        print(f"  + [{art['source']}] {art['title']}")
    if not items:
        raise SystemExit("модель не выбрала ни одной статьи из списка")
    stale = [r for r in db["items"] if r["id"] not in {i["id"] for i in items}]
    db["history"] = [history_entry(r) for r in stale] + db["history"]
    db["history"] = db["history"][:HISTORY_MAX]
    db["items"] = items
    chosen = {i["id"] for i in items}
    db["cache"] = [c for c in db["cache"] if c["id"] not in chosen]
    db["updatedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    db["model"] = model or db.get("model")
    save(db)
    return items


def history_entry(r):
    return {"id": r["id"], "title": r.get("title"), "url": r.get("url"), "source": r.get("source"),
            "reason": r.get("reason"), "verdict": r.get("verdict") or "new",
            "at": r.get("decidedAt") or r.get("suggestedAt") or today()}


# ---------------------------------------------------------------- вердикты
def verdict(rid, v):
    if v not in VERDICTS:
        raise SystemExit(f"bad verdict: {v}")
    db = load()
    if v == "read":  # дочитал то, что уже лежало в списке
        s = next((x for x in db["saved"] if x["id"] == rid), None)
        if not s:
            raise SystemExit(f"нет такой статьи в списке: {rid}")
        s["verdict"] = "read"
        s["decidedAt"] = today()
        db["saved"] = [x for x in db["saved"] if x["id"] != rid]
        db["history"] = ([history_entry(s)] + db["history"])[:HISTORY_MAX]
        save(db)
        return {"read": s}

    r = next((x for x in db["items"] if x["id"] == rid), None)
    if not r:
        raise SystemExit(f"нет такого совета: {rid}")
    r["verdict"] = v
    r["decidedAt"] = today()
    if v == "saved":
        db["saved"] = [dict(r, savedAt=today())] + [x for x in db["saved"] if x["id"] != rid]
    db["items"] = [x for x in db["items"] if x["id"] != rid]
    db["history"] = ([history_entry(r)] + db["history"])[:HISTORY_MAX]
    save(db)
    return {"rec": r, "saved": v == "saved"}


def due():
    db = load()
    if not db["items"]:
        return True, "советов ещё нет"
    if not db.get("updatedAt"):
        return True, "ни разу не запускался"
    try:
        last = datetime.datetime.fromisoformat(db["updatedAt"])
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
    f = sub.add_parser("fetch")
    f.add_argument("--days", type=int, default=45)
    sub.add_parser("digest")
    sub.add_parser("due")
    sub.add_parser("list")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=3)
    v = sub.add_parser("verdict")
    v.add_argument("id")
    v.add_argument("verdict", choices=VERDICTS)
    args = ap.parse_args()

    if args.cmd == "fetch":
        cache = fetch(args.days)
        print(f"кандидатов в кэше: {len(cache)}")
    elif args.cmd == "digest":
        print(digest())
    elif args.cmd == "due":
        ok, why = due()
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "apply":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        items = apply_answer(text, args.model, args.keep)
        print(f"{len(items)} статей сохранено в reads.json")
    elif args.cmd == "verdict":
        verdict(args.id, args.verdict)
        print(f"{args.id}: {args.verdict}")
    elif args.cmd == "list":
        db = load()
        print(f"обновлено {db.get('updatedAt')} ({db.get('model')}), кандидатов в кэше {len(db['cache'])}")
        for r in db["items"]:
            print(f"  {r['id']}  [{r['source']}] {r['title']}")
            if r.get("reason"):
                print(f"      {r['reason']}")
            print(f"      {r['url']}")
        if db["saved"]:
            print(f"  в списке чтения: {len(db['saved'])}")
            for s in db["saved"][:10]:
                print(f"    · [{s['source']}] {s['title']}")


if __name__ == "__main__":
    main()
