#!/usr/bin/env python3
"""Movie catalog CLI. Data lives in movies.json, posters in posters/.

Usage:
  movies.py search "query"                      find candidates on OMDb
  movies.py add "Title or tt1234567" [--rating 8] [--status watched|to-watch|watching] [--year 1999] [--watched-at 2026-09-06] [--note "..."]
  movies.py set "Title or tt…" [--rating 8] [--status …] [--watched-at …] [--note "..."]
  movies.py remove "Title or tt…"
  movies.py list [--status …]
  movies.py refresh "Title or tt…" | --all         re-fetch OMDb metadata + poster
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "movies.json")
POSTERS = os.path.join(ROOT, "posters")
API_KEY = os.environ.get("OMDB_API_KEY", "618ef0a4")
STATUSES = ("watched", "to-watch", "watching")


def load():
    if not os.path.exists(DATA):
        return []
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def save(movies):
    movies.sort(key=lambda m: (m.get("title") or "").lower())
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)
        f.write("\n")


def omdb(**params):
    params["apikey"] = API_KEY
    url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.load(r)
    if data.get("Response") == "False":
        raise SystemExit(f"OMDb: {data.get('Error')}")
    return data


def is_imdb(s):
    return bool(re.fullmatch(r"tt\d+", s.strip()))


def num(s, cast=float):
    try:
        if cast is int:
            return int(re.search(r"\d+", str(s)).group())
        return cast(str(s).replace(",", "").split()[0])
    except Exception:
        return None


def fetch_poster(imdb_id, url):
    if not url or url == "N/A":
        return None
    path = os.path.join(POSTERS, f"{imdb_id}.jpg")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            f.write(r.read())
        return f"posters/{imdb_id}.jpg"
    except Exception as e:
        print(f"  poster download failed for {imdb_id}: {e}", file=sys.stderr)
        return None


def metadata(imdb_id):
    d = omdb(i=imdb_id, plot="short")
    return {
        "id": d["imdbID"],
        "title": d.get("Title"),
        "year": num(d.get("Year"), int),
        "director": None if d.get("Director") in (None, "N/A") else d["Director"],
        "genre": [] if d.get("Genre") in (None, "N/A") else [g.strip() for g in d["Genre"].split(",")],
        "runtime": num(d.get("Runtime"), int),
        "plot": None if d.get("Plot") in (None, "N/A") else d["Plot"],
        "imdbRating": num(d.get("Rating") or d.get("imdbRating")),
        "posterUrl": None if d.get("Poster") in (None, "N/A") else d["Poster"],
        "type": d.get("Type"),
    }


def resolve(query, year=None):
    """Return an imdb id for a title query, asking OMDb."""
    if is_imdb(query):
        return query.strip()
    params = {"t": query}
    if year:
        params["y"] = year
    try:
        d = omdb(**params)
        return d["imdbID"]
    except SystemExit:
        res = omdb(s=query)["Search"]
        return res[0]["imdbID"]


def find(movies, query):
    q = query.strip().lower()
    for m in movies:
        if m["id"] == q or (m.get("title") or "").lower() == q:
            return m
    hits = [m for m in movies if q in (m.get("title") or "").lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit("Ambiguous, matches: " + ", ".join(f"{m['title']} ({m['year']}, {m['id']})" for m in hits))
    raise SystemExit(f"Not found in catalog: {query}")


def apply_opts(m, args):
    if getattr(args, "rating", None) is not None:
        m["rating"] = None if args.rating < 0 else args.rating
    if getattr(args, "status", None):
        m["status"] = args.status
    if getattr(args, "watched_at", None):
        m["watchedAt"] = args.watched_at
    if getattr(args, "note", None) is not None:
        m["note"] = args.note or None
    if m.get("status") == "to-watch":
        m["rating"] = None


def cmd_search(args):
    res = omdb(s=args.query)["Search"]
    for r in res:
        print(f"{r['imdbID']}  {r['Title']} ({r['Year']}) [{r['Type']}]")


def cmd_add(args):
    movies = load()
    imdb_id = resolve(args.query, args.year)
    if any(m["id"] == imdb_id for m in movies):
        raise SystemExit(f"Already in catalog: {imdb_id}. Use `set` to change it.")
    m = metadata(imdb_id)
    m["poster"] = fetch_poster(imdb_id, m["posterUrl"])
    m["rating"] = None
    m["status"] = args.status or ("watched" if args.rating is not None else "to-watch")
    m["addedAt"] = datetime.date.today().isoformat()
    m["watchedAt"] = None
    m["note"] = None
    apply_opts(m, args)
    movies.append(m)
    save(movies)
    print(f"Added: {m['title']} ({m['year']}) {m['id']} status={m['status']} rating={m['rating']}")


def cmd_set(args):
    movies = load()
    m = find(movies, args.query)
    apply_opts(m, args)
    save(movies)
    print(f"Updated: {m['title']} ({m['year']}) status={m['status']} rating={m['rating']}")


def cmd_remove(args):
    movies = load()
    m = find(movies, args.query)
    movies.remove(m)
    p = os.path.join(ROOT, m.get("poster") or "")
    if m.get("poster") and os.path.exists(p):
        os.remove(p)
    save(movies)
    print(f"Removed: {m['title']} ({m['year']})")


def cmd_list(args):
    movies = load()
    if args.status:
        movies = [m for m in movies if m["status"] == args.status]
    movies.sort(key=lambda m: (-(m.get("rating") or 0), (m.get("title") or "")))
    for m in movies:
        r = "" if m.get("rating") is None else f"{m['rating']:>4}"
        print(f"{r:>4}  {m['title']} ({m['year']})  [{m['status']}]  {m['id']}")


def cmd_refresh(args):
    movies = load()
    targets = movies if args.all else [find(movies, args.query)]
    for m in targets:
        fresh = metadata(m["id"])
        fresh["poster"] = fetch_poster(m["id"], fresh["posterUrl"]) or m.get("poster")
        m.update({k: v for k, v in fresh.items() if v not in (None, [])})
        print(f"Refreshed: {m['title']}")
    save(movies)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search"); s.add_argument("query"); s.set_defaults(fn=cmd_search)

    def common(sp):
        sp.add_argument("--rating", type=float, help="your rating 1-10 (use -1 to clear)")
        sp.add_argument("--status", choices=STATUSES)
        sp.add_argument("--watched-at", dest="watched_at", help="YYYY-MM-DD")
        sp.add_argument("--note")

    a = sub.add_parser("add"); a.add_argument("query"); a.add_argument("--year", type=int); common(a); a.set_defaults(fn=cmd_add)
    st = sub.add_parser("set"); st.add_argument("query"); common(st); st.set_defaults(fn=cmd_set)
    r = sub.add_parser("remove"); r.add_argument("query"); r.set_defaults(fn=cmd_remove)
    l = sub.add_parser("list"); l.add_argument("--status", choices=STATUSES); l.set_defaults(fn=cmd_list)
    rf = sub.add_parser("refresh"); rf.add_argument("query", nargs="?"); rf.add_argument("--all", action="store_true"); rf.set_defaults(fn=cmd_refresh)

    args = p.parse_args()
    if args.cmd == "refresh" and not args.all and not args.query:
        p.error("refresh needs a query or --all")
    args.fn(args)


if __name__ == "__main__":
    main()
