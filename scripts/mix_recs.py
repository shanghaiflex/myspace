#!/usr/bin/env python3
"""Daily mix recommendations. Claude Code (Opus) looks at the collection, at what was actually
played and at the verdicts on its previous advice, proposes new mixes as SoundCloud search
queries, and we resolve them into real tracks. Data lives in mix_recs.json.

Usage:
  mix_recs.py digest                     # the taste context the model sees
  mix_recs.py due                        # exit 0 when a run is due (used by mix_recs.sh)
  mix_recs.py apply --file answer.json [--model opus] [--keep 3]
  mix_recs.py verdict <id> liked|dismissed|played
  mix_recs.py list

The job runs daily on the mini (scripts/mix_recs.sh from launchd, deploy/install-mix-recs.sh)
and on demand from POST /api/mix-recs/refresh.
"""
import argparse, datetime, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "mix_recs.json")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import mixes as X  # noqa: E402

VERDICTS = ("new", "played", "liked", "dismissed")
MIN_MINUTES = 20
HISTORY_MAX = 120
EVERY_HOURS = 20  # a daily job that may fire late (the mini sleeps) should not refuse to run


def today():
    return datetime.date.today().isoformat()


def load():
    if not os.path.exists(DATA):
        return {"updatedAt": None, "model": None, "items": [], "history": []}
    with open(DATA, encoding="utf-8") as f:
        db = json.load(f)
    db.setdefault("items", [])
    db.setdefault("history", [])
    return db


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fmt_dur(s):
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60} мин"


# ---------------------------------------------------------------- digest
def digest():
    mixes = X.load()
    db = load()
    out = []

    out.append(f"## Моя коллекция ({len(mixes)} миксов)")
    for m in sorted(mixes, key=lambda m: m.get("addedAt") or "", reverse=True):
        bits = [b for b in (m.get("genre"), fmt_dur(m.get("duration")), m.get("published")) if b]
        out.append(f"- {m.get('artist') or '?'} — {m.get('title')} ({', '.join(bits)})")

    played = [m for m in mixes if m.get("playedAt")]
    played.sort(key=lambda m: m.get("playedAt") or 0, reverse=True)
    if played:
        out.append("\n## Что я реально слушал (последнее сверху)")
        for m in played[:12]:
            when = datetime.date.fromtimestamp(m["playedAt"]).isoformat()
            pos = m.get("position") or 0
            share = f", дослушал до {fmt_dur(pos)}" if pos else ", начал и бросил"
            if m.get("duration") and pos:
                share += f" из {fmt_dur(m['duration'])}"
            out.append(f"- {when}: {m.get('artist') or '?'} — {m.get('title')}{share}")
    else:
        out.append("\n## Что я реально слушал\n(пока нет данных о прослушивании)")

    if db["items"]:
        out.append("\n## Твои советы, которые сейчас висят у меня на странице (не повторяй их)")
        for r in db["items"]:
            out.append(f"- {r.get('artist')} — {r.get('title')}"
                       + (" (уже включал)" if r.get("played") else ""))
    if db["history"]:
        out.append("\n## Вердикты по прошлым советам (свежие сверху)")
        word = {"liked": "НРАВИТСЯ", "dismissed": "НЕ ТО", "played": "слушал", "new": "без ответа"}
        for h in db["history"][:40]:
            played_note = " (включал)" if h.get("played") and h.get("verdict") != "played" else ""
            out.append(f"- {h.get('at')} · {word.get(h.get('verdict'), h.get('verdict'))}{played_note}: "
                       f"{h.get('artist')} — {h.get('title')} — {h.get('reason') or ''}")
    else:
        out.append("\n## Вердикты по прошлым советам\n(это первый подбор, вердиктов ещё нет)")
    return "\n".join(out)


# ---------------------------------------------------------------- resolving candidates
def known_ids(db, mixes):
    ids = {m["id"] for m in mixes}
    ids |= {r["id"] for r in db["items"]}
    ids |= {h["id"] for h in db["history"]}
    return ids


def _words(s):
    return re.sub(r"[^0-9a-zA-Zа-яёА-ЯЁ]+", " ", s or "").lower().split()


def _match(m, artist):
    """How well a found track answers the asked-for artist: uploader hit > title hit > no hit.

    Without this the search happily returns the most-played track for the words in the query, and
    we would show a mix that has nothing to do with the reason the model wrote for it."""
    want = _words(artist)
    if not want:
        return 1
    if all(w in _words(m["artist"]) for w in want):
        return 2
    if all(w in _words(m["title"]) for w in want):
        return 1
    return 0


def search_soundcloud(query, artist=None, exclude=()):
    """Best long track for the query; the asked-for artist must actually be in it."""
    try:
        d = X.sc_api("/search/tracks", q=query, limit=20)
    except Exception as e:
        print(f"  search failed ({query}): {type(e).__name__}: {e}", file=sys.stderr)
        return None
    best = None
    for t in d.get("collection", []):
        if (t.get("kind") or "track") != "track":
            continue
        try:
            m = X.sc_track_to_mix(t)
        except Exception:
            continue
        if m["duration"] < MIN_MINUTES * 60 or m["id"] in exclude:
            continue
        hit = _match(m, artist)
        if not hit:
            continue
        score = (hit, t.get("playback_count") or 0)
        if best is None or score > best[0]:
            best = (score, m)
    return best[1] if best else None


def parse_answer(text):
    """The model is asked for a bare JSON array; be forgiving about fences and stray prose."""
    text = re.sub(r"```[a-z]*", "", text)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < i:
        raise SystemExit("no JSON array in the model answer")
    data = json.loads(text[i:j + 1])
    if not isinstance(data, list):
        raise SystemExit("model answer is not a list")
    return [c for c in data if isinstance(c, dict) and (c.get("search") or c.get("artist"))]


def apply_answer(text, model=None, keep=3):
    db = load()
    mixes = X.load()
    exclude = known_ids(db, mixes)
    items = []
    for c in parse_answer(text):
        if len(items) >= keep:
            break
        query = (c.get("search") or f"{c.get('artist','')} {c.get('title','')}").strip()
        m = search_soundcloud(query, c.get("artist"), exclude)
        if not m:
            print(f"  no long track for: {query}")
            continue
        exclude.add(m["id"])
        m.update(reason=(c.get("why") or "").strip() or None, wanted=query,
                 suggestedAt=today(), verdict="new", played=False)
        items.append(m)
        print(f"  + {m['artist']} — {m['title']} ({fmt_dur(m['duration'])})")
    if not items:
        raise SystemExit("nothing resolved on SoundCloud")
    # advice that is still unanswered goes to history as such: a new batch replaces it
    stale = [h for h in db["items"] if h["id"] not in {i["id"] for i in items}]
    db["history"] = [history_entry(r) for r in stale] + db["history"]
    db["history"] = db["history"][:HISTORY_MAX]
    db["items"] = items
    db["updatedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    db["model"] = model or db.get("model")
    save(db)
    return items


def history_entry(r):
    return {"id": r["id"], "artist": r.get("artist"), "title": r.get("title"), "url": r.get("url"),
            "reason": r.get("reason"), "verdict": r.get("verdict") or "new", "played": bool(r.get("played")),
            "at": r.get("decidedAt") or r.get("suggestedAt") or today()}


# ---------------------------------------------------------------- verdicts
def verdict(rid, v):
    if v not in VERDICTS:
        raise SystemExit(f"bad verdict: {v}")
    db = load()
    r = next((x for x in db["items"] if x["id"] == rid), None)
    if not r:
        raise SystemExit(f"no such suggestion: {rid}")
    if v == "played":  # implicit signal: stays on the page, the model gets to know it was tried
        r["played"] = True
        save(db)
        return {"rec": r, "mix": None}
    r["verdict"] = v
    r["decidedAt"] = today()
    added = None
    if v == "liked":
        mixes = X.load()
        if not any(m["id"] == r["id"] for m in mixes):
            added = {k: r.get(k) for k in ("id", "source", "url", "title", "artist", "duration",
                                           "artwork", "genre", "published")}
            added["tags"] = ["claude-rec"]
            added["addedAt"] = today()
            mixes.append(added)
            X.save(mixes)
    db["items"] = [x for x in db["items"] if x["id"] != rid]
    db["history"] = ([history_entry(r)] + db["history"])[:HISTORY_MAX]
    save(db)
    return {"rec": r, "mix": added}


def due():
    db = load()
    if not db["items"]:
        return True, "no suggestions yet"
    if not db.get("updatedAt"):
        return True, "never ran"
    try:
        last = datetime.datetime.fromisoformat(db["updatedAt"])
    except ValueError:
        return True, "bad updatedAt"
    hours = (datetime.datetime.now() - last).total_seconds() / 3600
    if hours >= EVERY_HOURS:
        return True, f"last run {hours:.1f}h ago"
    return False, f"last run {hours:.1f}h ago, next in {EVERY_HOURS - hours:.1f}h"


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digest")
    sub.add_parser("due")
    sub.add_parser("list")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True, help="the model answer (JSON array), - for stdin")
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=3)
    v = sub.add_parser("verdict")
    v.add_argument("id")
    v.add_argument("verdict", choices=VERDICTS)
    args = ap.parse_args()

    if args.cmd == "digest":
        print(digest())
    elif args.cmd == "due":
        ok, why = due()
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "apply":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        items = apply_answer(text, args.model, args.keep)
        print(f"{len(items)} suggestions saved to mix_recs.json")
    elif args.cmd == "verdict":
        r = verdict(args.id, args.verdict)
        print(f"{args.id}: {args.verdict}" + (" (added to the collection)" if r["mix"] else ""))
    elif args.cmd == "list":
        db = load()
        print(f"updated {db.get('updatedAt')} ({db.get('model')})")
        for r in db["items"]:
            print(f"  {r['id']}  {r.get('artist')} — {r.get('title')} ({fmt_dur(r.get('duration'))})"
                  + ("  [включал]" if r.get("played") else ""))
            if r.get("reason"):
                print(f"      {r['reason']}")
        if db["history"]:
            print(f"  history: {len(db['history'])} entries, "
                  f"{sum(1 for h in db['history'] if h['verdict'] == 'liked')} liked")


if __name__ == "__main__":
    main()
