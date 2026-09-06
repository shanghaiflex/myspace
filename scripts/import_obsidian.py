#!/usr/bin/env python3
"""One-time import from the Obsidian vault (Movies/data/*.md) into movies.json."""
import os, re, sys, glob, json, datetime
sys.path.insert(0, os.path.dirname(__file__))
import movies as M

VAULT = os.path.expanduser("~/Documents/Obsidian Vault/Movies/data")


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    fm, key = {}, None
    for line in (m.group(1) if m else "").splitlines():
        if re.match(r"^\s+-\s", line):
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(line.split("-", 1)[1].strip().strip('"'))
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip().strip('"')
            fm[key] = val if val != "" else None
    return fm


def main():
    existing = {m["id"]: m for m in M.load()}
    out = list(existing.values())
    for path in sorted(glob.glob(os.path.join(VAULT, "*.md"))):
        title = os.path.splitext(os.path.basename(path))[0]
        fm = parse_frontmatter(open(path, encoding="utf-8").read())
        tags = fm.get("tags") or []
        imdb_id = fm.get("imdbId")
        if not imdb_id:
            try:
                imdb_id = M.resolve(title, fm.get("year"))
            except SystemExit as e:
                print(f"SKIP {title}: {e}"); continue
        if imdb_id in existing:
            print(f"skip (exists) {title}"); continue
        try:
            m = M.metadata(imdb_id)
        except SystemExit as e:
            print(f"SKIP {title}: {e}"); continue
        m["poster"] = M.fetch_poster(imdb_id, m["posterUrl"] or fm.get("poster"))
        if not m.get("title"):
            m["title"] = title
        if fm.get("currentlyWatching") == "true":
            status = "watching"
        elif "to-watch" in tags:
            status = "to-watch"
        else:
            status = "watched"
        m["status"] = status
        m["rating"] = None if status == "to-watch" else M.num(fm.get("rating"))
        m["addedAt"] = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
        m["watchedAt"] = None
        m["note"] = None
        if fm.get("link"):
            m["link"] = fm["link"]
        out.append(m)
        existing[imdb_id] = m
        print(f"{status:9} {m['rating'] or '':>4}  {m['title']} ({m['year']})")
    M.save(out)
    print(f"\nTotal: {len(out)}")


if __name__ == "__main__":
    main()
