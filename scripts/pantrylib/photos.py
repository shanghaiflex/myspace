#!/usr/bin/env python3
"""Find a dish photo on Wikimedia Commons and cache it locally.

Commons was picked over Openverse and stock APIs because it needs no key, is
not rate-limited into uselessness for a daily job, and actually covers Russian
dishes (syrniki, ryazhenka) that stock libraries do not. Everything it returns
is freely licensed, and the credit travels with the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = "bodywithoutorgans-pantry/1.0 (personal site; contact via github.com/shanghaiflex)"
MIN_WIDTH = 900
# Dish forms: if the file name claims one the query never asked for, it is a
# different dish however well the ingredients line up.
FORMS = ("pasta", "spaghetti", "noodle", "pizza", "burger", "soup", "stew",
         "cake", "pie", "sandwich", "salad", "risotto", "curry", "sushi",
         "taco", "dumpling", "casserole", "smoothie", "cocktail")
THUMB_WIDTH = 1000
# Commons is full of packaging shots, diagrams and museum scans; these rarely
# look like food and never look appetising.
BAD = re.compile(r"logo|diagram|chart|map|label|packag|box|tin can|coat of arms|"
                 r"stamp|banknote|sign|poster|manuscript|painting|drawing|"
                 r"illustration|engraving|icon", re.I)


_last_call = 0.0
MIN_GAP = 3.0  # Commons answers 429 quickly for anonymous callers
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "commons_cache")


def _cached(key: str) -> dict | None:
    f = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ".json")
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _cache(key: str, value: dict) -> None:
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ".json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(value, fh)


def _get(params: dict, tries: int = 5) -> dict:
    """Query the API, politely and at most once per distinct question."""
    global _last_call
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    hit = _cached(url)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        gap = MIN_GAP - (time.time() - _last_call)
        if gap > 0:
            time.sleep(gap)
        try:
            _last_call = time.time()
            with urllib.request.urlopen(req, timeout=25) as r:
                out = json.load(r)
            _cache(url, out)
            return out
        except urllib.error.HTTPError as e:
            _last_call = time.time()
            if e.code not in (429, 503) or attempt == tries - 1:
                raise
            time.sleep(5 * 2 ** attempt)
    return {}


def search(query: str, limit: int = 12) -> list[str]:
    d = _get({"action": "query", "list": "search", "srnamespace": 6,
              "srsearch": f"{query} filetype:bitmap", "srlimit": limit})
    return [h["title"] for h in d.get("query", {}).get("search", [])]


def info(titles: list[str]) -> list[dict]:
    if not titles:
        return []
    d = _get({"action": "query", "prop": "imageinfo", "titles": "|".join(titles),
              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": THUMB_WIDTH})
    out = []
    for page in d.get("query", {}).get("pages", {}).values():
        ii = (page.get("imageinfo") or [None])[0]
        if not ii:
            continue
        meta = ii.get("extmetadata", {})
        out.append({
            "title": page.get("title", ""),
            "thumb": ii.get("thumburl") or ii.get("url"),
            "page": ii.get("descriptionurl"),
            "width": ii.get("width", 0), "height": ii.get("height", 0),
            "mime": ii.get("mime", ""),
            "artist": re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip(),
            "license": meta.get("LicenseShortName", {}).get("value", ""),
        })
    return out


def pick(query: str) -> dict | None:
    """Best photo for a dish: a real, wide-enough photograph, not a diagram.

    Commons search is AND-based, so a five-word dish name usually matches
    nothing. Drop words from the end until something comes back.
    """
    words = query.split()
    cands: list[dict] = []
    # At most three attempts: full name, then two progressively shorter ones.
    for n in sorted({len(words), max(2, len(words) - 1), 2}, reverse=True)[:3]:
        cands = info(search(" ".join(words[:n])))
        if cands:
            break
    if not cands:
        cands = info(search(words[0]))
    good = [c for c in cands
            if c["mime"] in ("image/jpeg", "image/png")
            and c["width"] >= MIN_WIDTH
            and 0.5 <= (c["width"] / max(c["height"], 1)) <= 2.6
            and not BAD.search(c["title"])
            # A file naming a dish form the query never asked for is a
            # different dish, however well the ingredients line up:
            # "creamy mushrooms with bacon" must not return carbonara.
            and not any(f in c["title"].lower() and f not in query.lower()
                        for f in FORMS)]
    if not good:
        return None

    # A file whose name repeats the query is far likelier to show the dish than
    # one that merely matched full-text: "Fried cheese omelet.jpg" over a
    # festival snapshot that happens to mention syrniki in its description.
    key_words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]

    def score(c: dict) -> int:
        t = c["title"].lower()
        return sum(w in t for w in key_words)

    good.sort(key=lambda c: (-score(c),
                             abs(c["width"] / max(c["height"], 1) - 1.5),
                             -min(c["width"], 2400)))
    # A photo of the wrong dish is worse than no photo at all.
    return good[0] if score(good[0]) > 0 else None


def fetch(query: str, out_dir: str) -> dict | None:
    """Download the chosen photo; returns {file, credit, license, page}."""
    p = pick(query)
    if not p:
        return None
    os.makedirs(out_dir, exist_ok=True)
    name = hashlib.sha1(p["thumb"].encode()).hexdigest()[:16] + ".jpg"
    path = os.path.join(out_dir, name)
    if not os.path.exists(path):
        req = urllib.request.Request(p["thumb"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as r, open(path, "wb") as f:
            f.write(r.read())
    return {"file": name, "credit": p["artist"][:80], "license": p["license"],
            "page": p["page"], "query": query}


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:]:
        r = pick(q)
        print(f"{q:<34} -> " + (f"{r['width']}x{r['height']} {r['license']:<12} {r['title'][:44]}"
                                if r else "НЕ НАЙДЕНО"))
