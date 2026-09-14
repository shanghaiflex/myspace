#!/usr/bin/env python3
"""Backgrounds for the site (mixes page picks one at random, crossfades on every new mix).

Source is Wikimedia Commons — no API key, and its featured categories hold genuinely large photographs
(4000–7000 px originals). Commons renders the scaled version for us, so nothing is resized locally and the
script needs no image library at all. Pexels and Unsplash would look better curated but both want a key;
if PEXELS_API_KEY ever appears in .env this is the place to add that source.

Candidates land in backgrounds/_candidates/ and are NOT used by the site until they are kept — the point is
that somebody (me or the model) looks at them first. Commons is full of documentary photographs that are
perfectly sharp and completely wrong behind a music player.

  python3 scripts/backgrounds.py fetch [--theme all] [--count 20] [--width 3840]   # candidates to look at
  python3 scripts/backgrounds.py themes                              # what themes there are
  python3 scripts/backgrounds.py keep <file>...                      # candidate -> backgrounds/
  python3 scripts/backgrounds.py keep --all
  python3 scripts/backgrounds.py drop <file>... | --all              # delete candidates
  python3 scripts/backgrounds.py list                                # what the site currently shows
  python3 scripts/backgrounds.py slim [--max-mb 4]                   # re-fetch too-heavy photos smaller
  python3 scripts/backgrounds.py remove <name>

Attribution: most Commons photos are CC-BY / CC-BY-SA, which asks for credit. There is nowhere on the page to
put a caption, so the author and licence of every kept photo are recorded in backgrounds/CREDITS.json.
"""
import argparse, hashlib, json, os, re, shutil, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG_DIR = os.path.join(ROOT, "backgrounds")
CAND_DIR = os.path.join(BG_DIR, "_candidates")
CREDITS = os.path.join(BG_DIR, "CREDITS.json")
API = "https://commons.wikimedia.org/w/api.php"
UA = "bodywithoutorgans-backgrounds/1.0 (personal site; contact via github)"

# Curated categories only. "Quality images" is far larger but mostly documentary — buildings, signs, insects.
# Not everything should be a view out of a window: a library, a vaulted ceiling or a nebula sits behind a
# player just as well and gets you out of the postcard mood. Fetch a theme by name, or all of them at once.
THEMES = {
    "nature": [
        "Featured pictures of landscapes",
        "Featured pictures of nature",
        "Featured pictures of sunsets",
        "Featured pictures of mountains",
        "Featured pictures of forests",
        "Featured pictures of clouds",
        "Featured pictures of seas",
    ],
    # уютное: тёплый свет, дерево, книги, залы, в которых хочется сидеть
    "interiors": [
        "Featured pictures of libraries",
        "Featured pictures of church interiors",
        "Featured pictures of mosque interiors",
        "Featured pictures of the Louvre",
        "Featured pictures of Moscow Metro",
        "Featured pictures of railway stations",
    ],
    # стильное: узор, витраж, роспись — картинка, а не пейзаж
    "art": [
        "Featured pictures of ceilings",
        "Featured pictures of stained-glass windows of churches",
        "Featured pictures of Islamic art",
        "Featured pictures of paintings in Paris",
        "Featured pictures of light painting",
    ],
    "space": [
        "Featured pictures of astronomy",
    ],
}
MIN_WIDTH = 2560          # anything smaller is not worth a full-screen background
MIN_RATIO, MAX_RATIO = 1.2, 2.4   # panoramas (13000x1486) and tall portraits crop to nothing on a wide screen
PAUSE = 2.0               # be polite to the API: it answers 429 to a faster walk through the categories


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("action", "query")
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(PAUSE * (attempt + 2))   # 429 means "slow down", not "give up"



def slug(title):
    """Commons file title -> a short, safe file name."""
    name = re.sub(r"^File:", "", title)
    name = os.path.splitext(name)[0]
    ascii_name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    # A title written entirely in a non-Latin script boils down to nothing (or to a stray number), and "01.jpg"
    # tells you neither what the photo is nor that it is unique. Fall back to a digest of the original title.
    if len(re.sub(r"[^a-z]", "", ascii_name)) < 3:
        ascii_name = "bg-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return ascii_name[:60].strip("-")


def known_ids():
    """Everything already downloaded or already rejected, so a re-run brings new photos."""
    ids = set()
    for d in (BG_DIR, CAND_DIR):
        if os.path.isdir(d):
            ids.update(os.path.splitext(f)[0] for f in os.listdir(d) if not f.startswith("."))
    ids.update(load_credits().keys())
    return ids


def load_credits():
    if os.path.exists(CREDITS):
        try:
            return json.load(open(CREDITS, encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def save_credits(data):
    json.dump(data, open(CREDITS, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def theme_categories(theme):
    if theme == "all":
        return [c for cats in THEMES.values() for c in cats]
    if theme not in THEMES:
        sys.exit(f"нет такой темы: {theme}. Есть: all, " + ", ".join(THEMES))
    return THEMES[theme]


def candidates(count, width, theme="all"):
    """Walk the curated categories and yield photos big enough and shaped right for a background.

    Round-robin по категориям, а не подряд: иначе первая же категория с сотней снимков съедает всю квоту
    и тема «уютное» приезжает одними потолками."""
    cats = theme_categories(theme)
    per_cat = max(1, count // len(cats) + 1)
    seen, out = known_ids(), []
    for cat in cats:
        if len(out) >= count:
            break
        cont, taken = None, 0
        while len(out) < count and taken < per_cat:
            params = dict(generator="categorymembers", gcmtitle="Category:" + cat, gcmlimit="50", gcmtype="file",
                          prop="imageinfo", iiprop="url|size|extmetadata", iiurlwidth=str(width))
            if cont:
                params["gcmcontinue"] = cont
            try:
                data = api(**params)
            except Exception as e:
                print(f"  {cat}: {e}", file=sys.stderr)
                break
            pages = (data.get("query") or {}).get("pages") or {}
            for p in pages.values():
                info = (p.get("imageinfo") or [None])[0]
                if not info:
                    continue
                w, h = info.get("width") or 0, info.get("height") or 0
                if w < MIN_WIDTH or not h:
                    continue
                if not (MIN_RATIO <= w / h <= MAX_RATIO):
                    continue
                name = slug(p["title"])
                if name in seen:
                    continue
                seen.add(name)
                meta = info.get("extmetadata") or {}
                out.append({"id": name, "title": p["title"], "url": info.get("thumburl") or info["url"],
                            "page": info.get("descriptionurl"), "orig": f"{w}x{h}",
                            "author": plain((meta.get("Artist") or {}).get("value")),
                            "licence": plain((meta.get("LicenseShortName") or {}).get("value"))})
                taken += 1
                if len(out) >= count or taken >= per_cat:
                    break
            cont = (data.get("continue") or {}).get("gcmcontinue")
            if not cont:
                break
            time.sleep(PAUSE)
    return out


def download(item, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, item["id"] if item["id"].endswith(".new") else item["id"] + ".jpg")
    req = urllib.request.Request(item["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    return path


def cmd_fetch(a):
    items = candidates(a.count, a.width, a.theme)
    if not items:
        print("новых кандидатов не нашлось")
        return
    os.makedirs(CAND_DIR, exist_ok=True)
    creds = load_credits()
    for it in items:
        try:
            p = download(it, CAND_DIR)
        except Exception as e:
            print(f"  не скачалось {it['id']}: {e}", file=sys.stderr)
            continue
        creds[it["id"]] = {"title": it["title"], "page": it["page"], "author": it["author"],
                           "licence": it["licence"], "orig": it["orig"], "kept": False}
        print(f"{os.path.getsize(p) // 1024:6} KB  {it['orig']:>11}  {it['id']}")
        time.sleep(PAUSE)
    save_credits(creds)
    print(f"\n{len(items)} кандидатов в {os.path.relpath(CAND_DIR, ROOT)}/ — посмотри и оставь нужные:"
          f"\n  python3 scripts/backgrounds.py keep <файл>...")


def cmd_keep(a):
    names = sorted(os.listdir(CAND_DIR)) if a.all else a.files
    creds = load_credits()
    for n in names:
        n = os.path.basename(n)
        src = os.path.join(CAND_DIR, n)
        if not os.path.exists(src):
            print(f"нет такого кандидата: {n}", file=sys.stderr)
            continue
        shutil.move(src, os.path.join(BG_DIR, n))
        creds.setdefault(os.path.splitext(n)[0], {})["kept"] = True
        print(f"оставлено: {n}")
    save_credits(creds)


def cmd_drop(a):
    names = sorted(os.listdir(CAND_DIR)) if a.all else a.files
    for n in names:
        n = os.path.basename(n)
        p = os.path.join(CAND_DIR, n)
        if os.path.exists(p):
            os.remove(p)
            print(f"выброшено: {n}")


def render_url(title, width):
    """URL of the Commons-rendered version of this file at the given width."""
    data = api(titles=title, prop="imageinfo", iiprop="url|size", iiurlwidth=str(width))
    page = next(iter((data.get("query") or {}).get("pages", {}).values()), {})
    info = (page.get("imageinfo") or [None])[0]
    if not info:
        return None
    return info.get("thumburl") or info.get("url")


def cmd_slim(a):
    """A background is a background, not a print: a 12 MB JPEG behind the player is a phone's data plan
    for nothing. Anything heavier than --max-mb is re-fetched at a smaller render of the same photo."""
    creds = load_credits()
    for f in sorted(os.listdir(BG_DIR)):
        if not f.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        path = os.path.join(BG_DIR, f)
        mb = os.path.getsize(path) / 1e6
        if mb <= a.max_mb:
            continue
        title = (creds.get(os.path.splitext(f)[0]) or {}).get("title")
        if not title:
            print(f"{f}: {mb:.1f} МБ, но источник неизвестен — оставляю", file=sys.stderr)
            continue
        for width in (2560, 1920, 1600):
            try:
                url = render_url(title, width)
                if not url:
                    break
                tmp = path + ".new"
                download({"id": os.path.basename(tmp), "url": url}, BG_DIR)
                new_mb = os.path.getsize(tmp) / 1e6
            except Exception as e:
                print(f"{f}: {e}", file=sys.stderr)
                break
            if new_mb <= a.max_mb or width == 1600:
                os.replace(tmp, path)
                print(f"{f}: {mb:.1f} → {new_mb:.1f} МБ ({width}px)")
                break
            os.remove(tmp)
            time.sleep(PAUSE)


def cmd_themes(a):
    for name, cats in THEMES.items():
        print(f"{name}:")
        for c in cats:
            print(f"  {c}")


def cmd_list(a):
    files = sorted(f for f in os.listdir(BG_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    creds = load_credits()
    total = 0
    for f in files:
        size = os.path.getsize(os.path.join(BG_DIR, f))
        total += size
        c = creds.get(os.path.splitext(f)[0]) or {}
        who = f"  {c['author']} · {c['licence']}" if c.get("author") else ""
        print(f"{size // 1024:6} KB  {f}{who}")
    print(f"\nвсего {len(files)}, {total // 1024 // 1024} МБ")


def cmd_remove(a):
    p = os.path.join(BG_DIR, os.path.basename(a.name))
    if not os.path.exists(p):
        sys.exit(f"нет такого фона: {a.name}")
    os.remove(p)
    creds = load_credits()
    creds.pop(os.path.splitext(os.path.basename(a.name))[0], None)
    save_credits(creds)
    print(f"удалено: {a.name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--count", type=int, default=20); p.add_argument("--width", type=int, default=3840)
    p.add_argument("--theme", default="all", help="all | " + " | ".join(THEMES))
    sub.add_parser("themes")
    p = sub.add_parser("keep"); p.add_argument("files", nargs="*"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("drop"); p.add_argument("files", nargs="*"); p.add_argument("--all", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("slim"); p.add_argument("--max-mb", type=float, default=4.0)
    p = sub.add_parser("remove"); p.add_argument("name")
    a = ap.parse_args(argv)
    {"fetch": cmd_fetch, "keep": cmd_keep, "drop": cmd_drop, "list": cmd_list, "remove": cmd_remove,
     "themes": cmd_themes, "slim": cmd_slim}[a.cmd](a)


if __name__ == "__main__":
    main()
