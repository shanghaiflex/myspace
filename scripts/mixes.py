#!/usr/bin/env python3
"""Mix catalog CLI. Data lives in mixes.json.

Usage:
  mixes.py add <url> [--tag ...]                soundcloud / youtube / mixcloud / nts.live URL
  mixes.py import-soundcloud <user> [--min-minutes 20] [--dry-run]
                                                import the user's SoundCloud likes, keep only long ones
  mixes.py remove <id|url|title>
  mixes.py list
"""
import argparse, datetime, json, os, re, subprocess, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "mixes.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"


def ytdlp():
    """Путь к yt-dlp. На mini он лежит в ~/bin, которого нет в PATH у неинтерактивного ssh
    и у launchd, поэтому ищем сами; переопределяется переменной YTDLP."""
    import shutil
    return (os.environ.get("YTDLP") or shutil.which("yt-dlp")
            or os.path.expanduser("~/bin/yt-dlp"))

def load():
    if not os.path.exists(DATA):
        return []
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def save(mixes):
    mixes.sort(key=lambda m: m.get("addedAt") or "", reverse=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(mixes, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- SoundCloud
_client_id = None


def sc_client_id():
    """Reuse the client_id yt-dlp discovers; make yt-dlp discover it if the cache is empty."""
    global _client_id
    if _client_id:
        return _client_id
    path = os.path.expanduser("~/.cache/yt-dlp/soundcloud/client_id.json")
    if not os.path.exists(path):
        subprocess.run([ytdlp(), "--flat-playlist", "--playlist-end", "1", "-J",
                        "https://soundcloud.com/resident-advisor/likes"], capture_output=True)
    with open(path) as f:
        _client_id = json.load(f)["data"]
    return _client_id


def sc_api(path, **params):
    params["client_id"] = sc_client_id()
    return get_json("https://api-v2.soundcloud.com" + path + "?" + urllib.parse.urlencode(params))


def sc_track_to_mix(t):
    art = (t.get("artwork_url") or t["user"].get("avatar_url") or "")
    return {
        "id": f"sc:{t['id']}",
        "source": "soundcloud",
        "url": t["permalink_url"],
        "title": t["title"],
        "artist": t["user"]["username"],
        "duration": round((t.get("full_duration") or t.get("duration") or 0) / 1000),
        "artwork": art.replace("-large.", "-t500x500.") if art else None,
        "genre": t.get("genre") or None,
        "published": (t.get("created_at") or "")[:10] or None,
    }


def resolve_soundcloud(url):
    d = sc_api("/resolve", url=url.split("?")[0])
    if d.get("kind") != "track":
        raise SystemExit(f"Not a SoundCloud track: {url} ({d.get('kind')})")
    return sc_track_to_mix(d)


# ---------------------------------------------------------------- yt-dlp (YouTube, Mixcloud, anything else)
def resolve_ytdlp(url):
    r = subprocess.run([ytdlp(), "-J", "--no-playlist", url], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"yt-dlp failed for {url}:\n{r.stderr.strip()[-400:]}")
    e = json.loads(r.stdout)
    ext = (e.get("extractor") or "").split("+")[0].lower()
    if ext.startswith("youtube"):
        source, mid = "youtube", f"yt:{e['id']}"
    elif ext.startswith("mixcloud"):
        source, mid = "mixcloud", f"mc:{e['id']}"
    elif ext.startswith("soundcloud"):
        source, mid = "soundcloud", f"sc:{e['id']}"
    else:
        raise SystemExit(f"Unsupported source '{ext}' for {url}. Supported: soundcloud, youtube, mixcloud, nts.live")
    up = e.get("upload_date")
    return {
        "id": mid,
        "source": source,
        "url": e.get("webpage_url") or url,
        "title": e.get("title"),
        "artist": re.sub(r"^Mixcloud ", "", e.get("uploader") or e.get("channel") or ""),
        "duration": round(e.get("duration") or 0),
        "artwork": e.get("thumbnail"),
        "genre": None,
        "published": f"{up[:4]}-{up[4:6]}-{up[6:]}" if up and len(up) == 8 else None,
    }


# ---------------------------------------------------------------- NTS
def resolve_nts(url):
    m = re.search(r"nts\.live/shows/([^/]+)/episodes/([^/?#]+)", url)
    if not m:
        raise SystemExit("NTS: expected a URL like https://www.nts.live/shows/<show>/episodes/<episode>")
    show, ep = m.groups()
    d = get_json(f"https://www.nts.live/api/v2/shows/{show}/episodes/{ep}")
    audio = [a["url"] for a in d.get("audio_sources") or []]
    target = next((a for a in audio if "soundcloud.com" in a), None) or d.get("mixcloud") \
        or next((a for a in audio if "mixcloud.com" in a), None)
    if not target:
        raise SystemExit("NTS: this episode has no SoundCloud/Mixcloud audio")
    mix = resolve(target)
    mix["title"] = d.get("name") or mix["title"]
    mix["artist"] = "NTS · " + (d.get("show_alias") or show).replace("-", " ").title()
    mix["artwork"] = (d.get("media") or {}).get("picture_medium_large") or mix["artwork"]
    mix["nts"] = url.split("?")[0]
    mix["published"] = (d.get("broadcast") or "")[:10] or mix["published"]
    return mix


def resolve(url):
    if "nts.live" in url:
        return resolve_nts(url)
    if "soundcloud.com" in url:
        return resolve_soundcloud(url)
    return resolve_ytdlp(url)


# ---------------------------------------------------------------- commands
def fmt_dur(s):
    return f"{s // 3600}:{s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60} min"


def add_mix(url, tags=None):
    mixes = load()
    mix = resolve(url.strip())
    if any(m["id"] == mix["id"] for m in mixes):
        raise SystemExit(f"Already in catalog: {mix['title']}")
    mix["tags"] = tags or []
    mix["addedAt"] = datetime.date.today().isoformat()
    mixes.append(mix)
    save(mixes)
    return mix


def cmd_add(args):
    m = add_mix(args.url, args.tag)
    print(f"Added [{m['source']}] {m['artist']} — {m['title']} ({fmt_dur(m['duration'])})")


def cmd_import_sc(args):
    mixes = load()
    known = {m["id"] for m in mixes}
    user = sc_api("/resolve", url=f"https://soundcloud.com/{args.user}")
    print(f"{user['username']}: {user.get('likes_count')} likes")
    href = f"https://api-v2.soundcloud.com/users/{user['id']}/likes"
    params = {"limit": 200, "client_id": sc_client_id()}
    added = skipped = 0
    while href:
        d = get_json(href + ("&" if "?" in href else "?") + urllib.parse.urlencode(params))
        for item in d.get("collection", []):
            t = item.get("track")
            if not t:
                continue
            mix = sc_track_to_mix(t)
            if mix["duration"] < args.min_minutes * 60:
                skipped += 1
                continue
            if mix["id"] in known:
                continue
            mix["tags"] = ["soundcloud-likes"]
            mix["addedAt"] = (item.get("created_at") or "")[:10] or datetime.date.today().isoformat()
            print(f"  + {mix['artist']} — {mix['title']} ({fmt_dur(mix['duration'])})")
            if not args.dry_run:
                mixes.append(mix)
                known.add(mix["id"])
            added += 1
        href = d.get("next_href")
    if not args.dry_run:
        save(mixes)
    print(f"\nAdded {added} mixes, skipped {skipped} tracks shorter than {args.min_minutes} min.")


def find(mixes, q):
    q = q.strip().lower()
    for m in mixes:
        if m["id"] == q or m["url"].lower().rstrip("/") == q.rstrip("/") or (m.get("title") or "").lower() == q:
            return m
    hits = [m for m in mixes if q in (m.get("title") or "").lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SystemExit("Ambiguous: " + "; ".join(f"{m['title']} ({m['id']})" for m in hits))
    raise SystemExit(f"Not found: {q}")


def cmd_remove(args):
    mixes = load()
    m = find(mixes, args.query)
    mixes.remove(m)
    save(mixes)
    print(f"Removed: {m['title']}")


def cmd_list(args):
    for m in load():
        print(f"{m['id']:<16} {fmt_dur(m['duration']):>7}  {m['artist']} — {m['title']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("url"); a.add_argument("--tag", action="append"); a.set_defaults(fn=cmd_add)
    i = sub.add_parser("import-soundcloud"); i.add_argument("user"); i.add_argument("--min-minutes", type=int, default=20)
    i.add_argument("--dry-run", action="store_true"); i.set_defaults(fn=cmd_import_sc)
    r = sub.add_parser("remove"); r.add_argument("query"); r.set_defaults(fn=cmd_remove)
    l = sub.add_parser("list"); l.set_defaults(fn=cmd_list)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
