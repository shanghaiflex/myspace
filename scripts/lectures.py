#!/usr/bin/env python3
"""Lecture tracker for YouTube lecture channels. Data lives in lectures.json; audio files in audio/ (gitignored).

Usage:
  lectures.py sync                       refresh the channel list (videos + streams + playlists), keep statuses
  lectures.py set <id|title> --status new|queued|listening|listened [--position SEC]
  lectures.py series "<name>" <id|title> ...   put lectures into a named (custom) series
  lectures.py audio <id|title> [...]     download audio-only (m4a) for offline/phone listening
  lectures.py list [--status ...]
  lectures.py add-channel <url>          add another channel to track
"""
import argparse, datetime, glob, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "lectures.json")
AUDIO = os.path.join(ROOT, "audio")
STATUSES = ("new", "queued", "listening", "listened")
DEFAULT_CHANNELS = [
    {"id": "UCFJjfwRP5CaKWxpbxjMzjsw", "name": "Семинары по истории Александра Макарова", "type": "youtube", "label": "Макаров · Средневековье"},
    {"id": "sc:589577313", "name": "Serj Bushwacker", "type": "soundcloud", "url": "https://soundcloud.com/serj-bushwacker", "label": "Bushwacker · Древний Египет"},
]


def load():
    if not os.path.exists(DATA):
        return {"channels": DEFAULT_CHANNELS, "series": {}, "lectures": []}
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def save(db):
    db["lectures"].sort(key=lambda l: (l.get("channel", ""), l.get("order", 0)))
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ytdlp_json(url, extra=()):
    r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", *extra, url], capture_output=True, text=True)
    if not r.stdout.strip():
        raise SystemExit(f"yt-dlp failed for {url}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def safe_id(vid):
    return vid.replace(":", "_")


def audio_path(vid):
    for ext in ("m4a", "webm", "opus", "mp3"):
        p = os.path.join(AUDIO, f"{safe_id(vid)}.{ext}")
        if os.path.exists(p):
            return p
    return None


def sync_soundcloud(db, ch, known, today):
    """Tracks of a SoundCloud user become lectures with ids sc:<trackid>."""
    import mixes as X
    import urllib.parse
    uid = ch["id"].split(":", 1)[1]
    href = f"https://api-v2.soundcloud.com/users/{uid}/tracks"
    params = {"limit": 200, "client_id": X.sc_client_id()}
    tracks = []
    while href:
        d = X.get_json(href + ("&" if "?" in href else "?") + urllib.parse.urlencode(params))
        tracks += d.get("collection", [])
        href = d.get("next_href")
    print(f"{ch['name']}: {len(tracks)} tracks")
    added = 0
    for i, t in enumerate(tracks):
        vid = f"sc:{t['id']}"
        l = known.get(vid)
        if not l:
            l = {"id": vid, "status": "new", "position": 0, "listenedAt": None, "addedAt": today, "note": None, "series": []}
            db["lectures"].append(l); known[vid] = l; added += 1
        art = (t.get("artwork_url") or t["user"].get("avatar_url") or "")
        l.update({"title": (t.get("title") or "").strip(), "duration": round((t.get("full_duration") or t.get("duration") or 0) / 1000),
                  "channel": ch["id"], "order": i, "source": "soundcloud", "url": t["permalink_url"],
                  "artwork": art.replace("-large.", "-t500x500.") if art else None,
                  "published": (t.get("created_at") or "")[:10] or None})
    print(f"  new: {added}")


def has_audio(vid):
    return audio_path(vid) is not None


# ---------------------------------------------------------------- commands
def cmd_sync(args):
    db = load()
    known = {l["id"]: l for l in db["lectures"]}
    today = datetime.date.today().isoformat()
    for ch in db["channels"]:
        if ch.get("type") == "soundcloud":
            sync_soundcloud(db, ch, known, today)
            continue
        base = f"https://www.youtube.com/channel/{ch['id']}"
        entries = []
        for tab in ("videos", "streams"):
            try:
                entries += ytdlp_json(f"{base}/{tab}").get("entries") or []
            except SystemExit as e:
                print(f"  {tab}: {e}", file=sys.stderr)
        print(f"{ch['name']}: {len(entries)} videos")
        series_of = {}
        if not args.no_playlists:
            pls = ytdlp_json(f"{base}/playlists").get("entries") or []
            for pl in pls:
                pid, title = pl.get("id"), (pl.get("title") or "").strip()
                if not pid:
                    continue
                db["series"][pid] = title
                try:
                    items = ytdlp_json(f"https://www.youtube.com/playlist?list={pid}").get("entries") or []
                except SystemExit:
                    continue
                for it in items:
                    series_of.setdefault(it.get("id"), []).append(pid)
            print(f"  playlists: {len(pls)}")
        added = 0
        for i, e in enumerate(entries):
            vid = e.get("id")
            if not vid:
                continue
            l = known.get(vid)
            if not l:
                l = {"id": vid, "status": "new", "position": 0, "listenedAt": None, "addedAt": today, "note": None}
                db["lectures"].append(l); known[vid] = l; added += 1
            l.update({"title": (e.get("title") or "").strip(), "duration": round(e.get("duration") or 0),
                      "channel": ch["id"], "order": i, "live": (e.get("live_status") == "is_live"), "source": "youtube",
                      "url": f"https://www.youtube.com/watch?v={vid}"})
            if series_of:
                l["series"] = series_of.get(vid, [])
        print(f"  new: {added}")
    save(db)


def find(db, q):
    q = q.strip()
    m = re.search(r"(?:v=|youtu\.be/|/live/)([\w-]{11})", q)
    if m:
        q = m.group(1)
    if "soundcloud.com/" in q:
        hit = next((l for l in db["lectures"] if l.get("url", "").rstrip("/") == q.split("?")[0].rstrip("/")), None)
        if hit:
            return hit
    for l in db["lectures"]:
        if l["id"] == q or l["title"].lower() == q.lower():
            return l
    hits = [l for l in db["lectures"] if q.lower() in l["title"].lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SystemExit("Ambiguous: " + "; ".join(f"{l['title']} ({l['id']})" for l in hits[:8]))
    raise SystemExit(f"Not found: {q}")


def cmd_set(args):
    db = load()
    l = find(db, args.query)
    if args.status:
        l["status"] = args.status
        if args.status == "listened":
            l["listenedAt"] = l.get("listenedAt") or datetime.date.today().isoformat()
        if args.status == "queued":
            l["queuedAt"] = l.get("queuedAt") or int(datetime.datetime.now().timestamp())
        if args.status == "new":
            l["position"] = 0
    if args.position is not None:
        l["position"] = max(0, int(args.position))
    if args.note is not None:
        l["note"] = args.note or None
    save(db)
    print(f"{l['status']:9} {l['title']}")


def download_audio(vid):
    os.makedirs(AUDIO, exist_ok=True)
    if has_audio(vid):
        return audio_path(vid)
    if vid.startswith("sc:"):
        url, fmt = f"https://api.soundcloud.com/tracks/{vid[3:]}", "bestaudio"
    else:
        url, fmt = f"https://www.youtube.com/watch?v={vid}", "140/bestaudio[ext=m4a]/bestaudio"
    r = subprocess.run(["yt-dlp", "-f", fmt, "--no-playlist", "--no-progress",
                        "-o", os.path.join(AUDIO, f"{safe_id(vid)}.%(ext)s"), url], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-400:])
    return audio_path(vid)


def cmd_audio(args):
    db = load()
    for q in args.query:
        l = find(db, q)
        print(f"downloading {l['title']} …", flush=True)
        try:
            p = download_audio(l["id"])
            print(f"  → {os.path.relpath(p, ROOT)} ({os.path.getsize(p) // 1_000_000} MB)")
        except RuntimeError as e:
            print(f"  failed: {e}", file=sys.stderr)


def cmd_series(args):
    db = load()
    sid = "custom:" + re.sub(r"[^a-z0-9а-яё]+", "-", args.name.lower()).strip("-")
    db["series"][sid] = args.name
    for q in args.query:
        l = find(db, q)
        l.setdefault("series", [])
        if sid not in l["series"]:
            l["series"].append(sid)
        print(f"  {l['title']}")
    save(db)
    print(f"series '{args.name}' ({sid})")


def cmd_list(args):
    db = load()
    for l in db["lectures"]:
        if args.status and l["status"] != args.status:
            continue
        d = l.get("duration") or 0
        a = "♪" if has_audio(l["id"]) else " "
        print(f"{l['status']:9} {a} {d // 3600}:{d % 3600 // 60:02d}  {l['id']:<14} {l['title']}")


def cmd_add_channel(args):
    db = load()
    info = ytdlp_json(args.url, ("--playlist-items", "0"))
    cid, name = info.get("channel_id") or info.get("id"), info.get("channel") or info.get("title")
    if any(c["id"] == cid for c in db["channels"]):
        raise SystemExit("already tracked")
    db["channels"].append({"id": cid, "name": name})
    save(db)
    print(f"added {name} ({cid}); run sync")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--no-playlists", action="store_true"); s.set_defaults(fn=cmd_sync)
    st = sub.add_parser("set"); st.add_argument("query"); st.add_argument("--status", choices=STATUSES)
    st.add_argument("--position", type=int); st.add_argument("--note"); st.set_defaults(fn=cmd_set)
    a = sub.add_parser("audio"); a.add_argument("query", nargs="+"); a.set_defaults(fn=cmd_audio)
    se = sub.add_parser("series"); se.add_argument("name"); se.add_argument("query", nargs="+"); se.set_defaults(fn=cmd_series)
    l = sub.add_parser("list"); l.add_argument("--status", choices=STATUSES); l.set_defaults(fn=cmd_list)
    c = sub.add_parser("add-channel"); c.add_argument("url"); c.set_defaults(fn=cmd_add_channel)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
