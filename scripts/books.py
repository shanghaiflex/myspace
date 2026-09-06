#!/usr/bin/env python3
"""Book catalog CLI. Data lives in books.json, covers in covers/.

Usage:
  books.py search "query"                        Google Books candidates
  books.py add "Title" [--author A] [--status read|reading|to-read|abandoned] [--rating 8] [--isbn 978...]
  books.py set "Title|id" [--status ...] [--rating N] [--comment "..."] [--finished-at YYYY-MM-DD]
  books.py remove "Title|id"
  books.py list [--status ...]
  books.py import-obsidian                       one-time import from the Obsidian vault (Books/data/*.md)
"""
import argparse, datetime, glob, json, os, re, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "books.json")
COVERS = os.path.join(ROOT, "covers")
STATUSES = ("read", "reading", "to-read", "abandoned")
VAULT = os.path.expanduser("~/Documents/Obsidian Vault/Books/data")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"


def load():
    if not os.path.exists(DATA):
        return []
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def save(books):
    books.sort(key=lambda b: (b.get("title") or "").lower())
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=2)
        f.write("\n")


def slug(s):
    s = re.sub(r"[^a-z0-9а-яё]+", "-", (s or "").lower()).strip("-")
    return s[:60] or "book"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_cover(bid, url):
    if not url:
        return None
    os.makedirs(COVERS, exist_ok=True)
    url = url.replace("&zoom=1", "&zoom=2").replace("&edge=curl", "")
    path = os.path.join(COVERS, f"{bid}.jpg")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 1000:
            return None
        with open(path, "wb") as f:
            f.write(data)
        return f"covers/{bid}.jpg"
    except Exception as e:
        print(f"  cover failed for {bid}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- Google Books
def gb_search(q, author=None, isbn=None, n=5):
    if isbn:
        q = f"isbn:{isbn}"
    elif author:
        q = f"intitle:{q} inauthor:{author}"
    d = get_json("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": q, "maxResults": n}))
    out = []
    for it in d.get("items") or []:
        v = it.get("volumeInfo") or {}
        ids = {i["type"]: i["identifier"] for i in v.get("industryIdentifiers", [])}
        out.append({
            "gbId": it.get("id"), "title": v.get("title"), "subtitle": v.get("subtitle"),
            "author": ", ".join(v.get("authors") or []) or None,
            "year": int(v["publishedDate"][:4]) if v.get("publishedDate", "")[:4].isdigit() else None,
            "pages": v.get("pageCount"), "isbn": ids.get("ISBN_13") or ids.get("ISBN_10"),
            "coverUrl": (v.get("imageLinks") or {}).get("thumbnail"), "language": v.get("language"),
            "description": v.get("description"),
        })
    return out


def ol_search(q, author=None, isbn=None, n=5):
    params = {"limit": n, "fields": "key,title,author_name,first_publish_year,isbn,cover_i,number_of_pages_median,language"}
    if isbn:
        params["isbn"] = isbn
    else:
        params["title"] = q
        if author:
            params["author"] = author
    d = get_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params))
    out = []
    for doc in d.get("docs") or []:
        isbns = [i for i in doc.get("isbn") or [] if len(i) == 13] or (doc.get("isbn") or [])
        out.append({"gbId": doc.get("key"), "title": doc.get("title"), "subtitle": None, "author": ", ".join(doc.get("author_name") or []) or None,
                    "year": doc.get("first_publish_year"), "pages": doc.get("number_of_pages_median"), "isbn": isbns[0] if isbns else None,
                    "coverUrl": f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg" if doc.get("cover_i") else None,
                    "language": (doc.get("language") or [None])[0], "description": None})
    return out


def lookup(q, author=None, isbn=None, n=5):
    """Google Books first (best for Russian titles), Open Library as fallback (Google rate-limits some IPs)."""
    errors = []
    for fn in (gb_search, ol_search):
        try:
            res = fn(q, author, isbn, n)
            if res:
                return res
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    if errors:
        print("lookup problems: " + "; ".join(errors), file=sys.stderr)
    return []


def find(books, q):
    q = q.strip().lower()
    for b in books:
        if b["id"] == q or (b.get("title") or "").lower() == q:
            return b
    hits = [b for b in books if q in (b.get("title") or "").lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SystemExit("Ambiguous: " + "; ".join(f"{b['title']} ({b['id']})" for b in hits[:8]))
    raise SystemExit(f"Not found: {q}")


def apply(b, args):
    if getattr(args, "status", None):
        b["status"] = args.status
        if args.status == "read":
            b["finishedAt"] = b.get("finishedAt") or datetime.date.today().isoformat()
    if getattr(args, "rating", None) is not None:
        b["rating"] = None if args.rating < 0 else args.rating
    if getattr(args, "comment", None) is not None:
        b["comment"] = args.comment or None
    if getattr(args, "finished_at", None):
        b["finishedAt"] = args.finished_at


# ---------------------------------------------------------------- commands
def cmd_search(args):
    for r in lookup(args.query, args.author):
        print(f"{r['gbId']}  {r['title']} — {r['author']} ({r['year']}) isbn={r['isbn']}")


def cmd_add(args):
    books = load()
    if args.manual:
        r = {"title": args.query, "author": args.author, "year": args.year, "pages": None, "isbn": args.isbn, "coverUrl": args.cover, "description": None}
    else:
        res = lookup(args.query, args.author, args.isbn, 1)
        if not res:
            raise SystemExit("Nothing found in Google Books / Open Library. Retry with --isbn, or add by hand: --manual --author ... --year ... --cover URL")
        r = res[0]
    bid = r["isbn"] or slug(f"{r['title']}-{r['author']}")
    if any(b["id"] == bid for b in books):
        raise SystemExit(f"Already in catalog: {r['title']}")
    b = {"id": bid, "title": r["title"], "author": r["author"], "year": r["year"], "pages": r["pages"], "isbn": r["isbn"],
         "description": (r.get("description") or "")[:600] or None, "coverUrl": r["coverUrl"],
         "cover": fetch_cover(bid, r["coverUrl"]), "status": "to-read", "rating": None, "comment": None,
         "addedAt": datetime.date.today().isoformat(), "finishedAt": None, "tags": []}
    apply(b, args)
    books.append(b)
    save(books)
    print(f"Added: {b['title']} — {b['author']} ({b['year']}) status={b['status']}")


def cmd_set(args):
    books = load()
    b = find(books, args.query)
    apply(b, args)
    save(books)
    print(f"Updated: {b['title']} status={b['status']} rating={b['rating']}")


def cmd_remove(args):
    books = load()
    b = find(books, args.query)
    books.remove(b)
    p = os.path.join(ROOT, b.get("cover") or "")
    if b.get("cover") and os.path.exists(p):
        os.remove(p)
    save(books)
    print(f"Removed: {b['title']}")


def cmd_list(args):
    for b in load():
        if args.status and b["status"] != args.status:
            continue
        r = "" if b.get("rating") is None else f"{b['rating']:>4}"
        print(f"{b['status']:9} {r:>4}  {b['title']} — {b.get('author') or '?'}  [{b['id']}]")


def parse_note(text):
    fm = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9 ]*?)::\s*(.*)$", line)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip()
    return fm


def cmd_import(args):
    books = load()
    known = {b["id"] for b in books}
    for path in sorted(glob.glob(os.path.join(VAULT, "*.md"))):
        fm = parse_note(open(path, encoding="utf-8").read())
        title = re.sub(r"^(#\s*)+", "", fm.get("Title") or os.path.splitext(os.path.basename(path))[0]).strip()
        author = fm.get("Author") or None
        isbn = fm.get("ISBN13") or fm.get("ISBN10") or ""
        bid = isbn if re.fullmatch(r"\d{10,13}", isbn) else slug(f"{title}-{author or ''}")
        if bid in known:
            print(f"skip (exists) {title}"); continue
        if fm.get("Read", "").strip() == "1":
            status = "read"
        elif fm.get("NotFinished", "").lower() == "true":
            status = "abandoned"
        elif fm.get("CurrentlyReading", "").lower() == "true":
            status = "reading"
        else:
            status = "to-read"
        rating = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*10", fm.get("Rating", ""))
        year = re.search(r"\d{4}", fm.get("Publish date", "") or "")
        cover_url = fm.get("Cover") if (fm.get("Cover") or "").startswith("http") else None
        tag = re.sub(r"^#📥/📚/", "", fm.get("Tags", "")).strip()
        added = fm.get("Date") or datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
        b = {"id": bid, "title": title, "author": author, "year": int(year.group()) if year else None, "pages": None,
             "isbn": isbn or None, "description": None, "coverUrl": cover_url, "cover": fetch_cover(bid, cover_url),
             "status": status, "rating": float(rating.group(1)) if rating else None,
             "comment": (fm.get("Comment") or "").strip() or None, "addedAt": added[:10],
             "finishedAt": None, "tags": [tag] if tag else []}
        books.append(b); known.add(bid)
        print(f"{status:9}  {title} — {author}  cover={'yes' if b['cover'] else 'no'}")
    save(books)
    print(f"\nTotal: {len(books)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--author"); s.set_defaults(fn=cmd_search)

    def common(sp):
        sp.add_argument("--status", choices=STATUSES); sp.add_argument("--rating", type=float)
        sp.add_argument("--comment"); sp.add_argument("--finished-at", dest="finished_at")
    a = sub.add_parser("add"); a.add_argument("query"); a.add_argument("--author"); a.add_argument("--isbn")
    a.add_argument("--manual", action="store_true", help="don't look up, use the given fields"); a.add_argument("--year", type=int); a.add_argument("--cover", help="cover image URL")
    common(a); a.set_defaults(fn=cmd_add)
    st = sub.add_parser("set"); st.add_argument("query"); common(st); st.set_defaults(fn=cmd_set)
    r = sub.add_parser("remove"); r.add_argument("query"); r.set_defaults(fn=cmd_remove)
    l = sub.add_parser("list"); l.add_argument("--status", choices=STATUSES); l.set_defaults(fn=cmd_list)
    i = sub.add_parser("import-obsidian"); i.set_defaults(fn=cmd_import)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
