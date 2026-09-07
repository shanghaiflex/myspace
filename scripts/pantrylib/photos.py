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
MEALDB = "https://www.themealdb.com/api/json/v1/1"
MEALDB_MIN_OVERLAP = 2  # one shared word is coincidence, two is a real match
# Words that describe every second dish and so prove nothing: matching on
# "creamy" + "sauce" once returned a jar of green herb sauce for pelmeni.
STOP = {"creamy", "cream", "sauce", "fresh", "homemade", "easy", "quick",
        "style", "dish", "plate", "hot", "cold", "soft", "with", "and",
        "the", "for", "served", "sliced", "chopped", "mixed", "special"}
PEXELS = "https://api.pexels.com/v1/search"
UA = "bodywithoutorgans-pantry/1.0 (personal site; contact via github.com/shanghaiflex)"
MIN_WIDTH = 900
# Dish forms: if the file name claims one the query never asked for, it is a
# different dish however well the ingredients line up.
FORMS = ("pasta", "spaghetti", "noodle", "pizza", "burger", "soup", "stew",
         "cake", "pie", "sandwich", "salad", "risotto", "curry", "sushi",
         "taco", "dumpling", "casserole", "smoothie", "cocktail", "stuffed",
         "pancake", "waffle", "wrap", "kebab", "meatball", "pudding",
         "porridge", "tart", "muffin", "brownie", "roast", "skewer", "fritter")
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
    nothing. Shorten the query step by step, but keep collecting instead of
    stopping at the first non-empty answer -- "dumplings in creamy mushroom"
    returns exactly one photo of a shop menu, while plain "dumplings" returns
    a dozen good ones.
    """
    words = query.split()
    key_words = [w for w in re.findall(r"\w+", query.lower())
                 if len(w) > 3 and w not in STOP]

    def relevance(c: dict) -> int:
        t = c["title"].lower()
        return sum(w in t for w in key_words)

    def usable(c: dict, strict: bool) -> bool:
        if c["mime"] not in ("image/jpeg", "image/png"):
            return False
        if c["width"] < MIN_WIDTH:
            return False
        if not 0.5 <= (c["width"] / max(c["height"], 1)) <= 2.6:
            return False
        if BAD.search(c["title"]):
            return False
        # A file naming a dish form the query never asked for is a different
        # dish, however well the ingredients line up: "creamy mushrooms with
        # bacon" must not return carbonara. Relaxed on the second pass -- a
        # loosely related photo still beats an empty card.
        if strict and any(f in c["title"].lower() and f not in query.lower()
                          for f in FORMS):
            return False
        return True

    seen: set[str] = set()
    cands: list[dict] = []
    for n in sorted({len(words), max(2, len(words) - 1), 2, 1}, reverse=True):
        for c in info(search(" ".join(words[:n]))):
            if c["title"] not in seen:
                seen.add(c["title"])
                cands.append(c)
        if any(usable(c, True) and relevance(c) > 0 for c in cands):
            break

    good = [c for c in cands if usable(c, True)] or \
           [c for c in cands if usable(c, False)]
    good = [c for c in good if relevance(c) > 0]
    if not good:
        return None
    # Landscape-ish and large reads best in a card.
    good.sort(key=lambda c: (-relevance(c),
                             abs(c["width"] / max(c["height"], 1) - 1.5),
                             -min(c["width"], 2400)))
    return good[0]


def _json(url: str, headers: dict | None = None) -> dict:
    hit = _cached(url)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode("utf-8", "replace")
    # TheMealDB embeds raw newlines inside JSON strings.
    out = json.loads(raw, strict=False)
    _cache(url, out)
    return out


def from_mealdb(query: str) -> dict | None:
    """Studio food photography, no key needed -- but a small, Western catalogue.

    Tried before Commons because Commons food is overwhelmingly amateur
    snapshots: closer to the dish, far less appetising. Since every photo is
    labelled an illustration anyway, looking good matters more than matching
    exactly.
    """
    words = [w for w in re.findall(r"[a-z]+", query.lower())
             if len(w) > 2 and w not in STOP]
    attempts = [f"{MEALDB}/search.php?s={urllib.parse.quote(query)}"]
    attempts += [f"{MEALDB}/search.php?s={urllib.parse.quote(w)}" for w in words[:3]]
    attempts += [f"{MEALDB}/filter.php?i={urllib.parse.quote(w)}" for w in words[:3]]
    for url in attempts:
        try:
            meals = (_json(url) or {}).get("meals") or []
        except Exception:
            continue
        if not meals:
            continue
        # Prefer a meal whose name echoes the query -- and take it only if it
        # really does. A gorgeous photo of the wrong dish ("Codfish deviled
        # eggs" for scrambled eggs with bacon) reads as a bug, not as styling.
        def overlap(m: dict) -> int:
            name = m.get("strMeal", "").lower()
            if any(f in name and f not in query.lower() for f in FORMS):
                return -1
            return sum(w in name for w in words)

        meals.sort(key=lambda m: -overlap(m))
        m = meals[0]
        thumb = m.get("strMealThumb")
        if thumb and overlap(m) >= MEALDB_MIN_OVERLAP:
            return {"thumb": thumb, "artist": "TheMealDB", "license": "",
                    "page": f"https://www.themealdb.com/meal/{m.get('idMeal', '')}",
                    "title": m.get("strMeal", "")}
    return None


def from_pexels(query: str) -> dict | None:
    """Best-looking option, but needs a free key in PEXELS_API_KEY."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    url = (f"{PEXELS}?per_page=1&orientation=landscape&query="
           + urllib.parse.quote(query))
    try:
        d = _json(url, {"Authorization": key})
    except Exception:
        return None
    ps = d.get("photos") or []
    if not ps:
        return None
    p0 = ps[0]
    return {"thumb": p0["src"]["large"], "artist": p0.get("photographer", ""),
            "license": "Pexels", "page": p0.get("url", ""),
            "title": p0.get("alt", "")}


def candidates(query: str, limit: int = 6) -> list[dict]:
    """Everything worth looking at, from every source, for a human to judge.

    The automatic picker guesses from file names and gets it wrong in ways only
    eyes catch: a carbonara for "creamy mushrooms", a jar of green sauce for
    pelmeni. This hands the choice to something that can actually see.
    """
    out: list[dict] = []
    px = from_pexels(query)
    if px:
        out.append({**px, "source": "Pexels"})
    md = from_mealdb(query)
    if md:
        out.append({**md, "source": "TheMealDB"})

    words = query.split()
    seen = {c.get("title") for c in out}
    for n in sorted({len(words), max(2, len(words) - 1), 2, 1}, reverse=True):
        for c in info(search(" ".join(words[:n]))):
            if (c["title"] in seen or c["mime"] not in ("image/jpeg", "image/png")
                    or c["width"] < MIN_WIDTH or BAD.search(c["title"])):
                continue
            seen.add(c["title"])
            out.append({**c, "source": "Commons"})
            if len(out) >= limit:
                return out
    return out


def download(url: str, path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r, open(path, "wb") as f:
        f.write(r.read())


def fetch(query: str, out_dir: str) -> dict | None:
    """Download the best photo we can get; returns {file, credit, license, page}.

    Order is by looks, not by literal accuracy: Pexels (if a key is set),
    then TheMealDB, then Commons as the always-available fallback.
    """
    p = from_pexels(query) or from_mealdb(query) or pick(query)
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
