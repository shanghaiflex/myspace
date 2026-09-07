#!/usr/bin/env python3
"""Pantry CLI: keep pantry.json fresh and feed the daily note to Claude.

    python3 scripts/pantry.py sync            # pull new receipts from Gmail
    python3 scripts/pantry.py summary         # recompute stock -> pantry.json
    python3 scripts/pantry.py profile         # what the model sees when inventing dishes

Mirrors scripts/health.py: the pipeline is deterministic, the model only writes
the dish ideas at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pantrylib import inventory as inv  # noqa: E402
from pantrylib import photos as ph  # noqa: E402
from pantrylib import receipts as rec  # noqa: E402
from pantrylib.analyze import build, load_receipts, stats  # noqa: E402
from pantrylib.shelf_life import guess  # noqa: E402

STATE = os.path.join(ROOT, "pantry.json")
PHOTO_DIR = os.path.join(ROOT, "dishes")
MAX_INGREDIENTS = 6
MAX_STEPS = 4
RECIPES_STALE_DAYS = 5
KEEP_REJECTED = 40
WANT_RECIPES = 4


def load() -> dict:
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(st: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def profile_text() -> str:
    """What this person actually eats, straight out of five years of receipts.

    This is the whole context for the recipe ideas: no questionnaire, no
    guessing -- the shopping basket is the diet.
    """
    import collections
    rows = stats(build(load_receipts()), 0)
    cats = collections.Counter()
    for r in rows:
        cats[guess(r["name"])["category"]] += r["buys"]
    total = sum(cats.values()) or 1

    out = ["## Структура моих покупок (доля позиций)"]
    for cat, n in cats.most_common(12):
        out.append(f"- {cat}: {n * 100 // total}%")
    st = load()
    kept = [r["title"] for r in (st.get("recipes") or [])]
    if kept:
        out.append("")
        out.append("## Уже на странице — не повторяй и не предлагай вариации")
        out += [f"- {t}" for t in kept]
    rejected = st.get("rejected") or []
    if rejected:
        out.append("")
        out.append("## Я это отклонил — не предлагай снова, и близкое тоже")
        out += [f"- {t}" for t in rejected]
    out.append("")
    out.append("## Что я покупаю регулярно")
    for r in [x for x in rows if x["regular"]][:35]:
        out.append(f"- {r['name']} (раз в {r['cadence_days']:.0f} дн.)")
    return "\n".join(out)


def cmd_profile(a) -> None:
    print(profile_text())


def cmd_reject(a) -> None:
    """Drop a dish and remember it: the same idea should not come back.

    Mirrors the mix advice verdicts -- feedback is the point, otherwise the
    model re-proposes what was already turned down.
    """
    st = load()
    recipes = st.get("recipes") or []
    q = a.title.lower()
    hit = [r for r in recipes if q in r["title"].lower()]
    if not hit:
        sys.exit(f"не нашёл блюдо по «{a.title}». Есть: "
                 + ", ".join(f"«{r['title']}»" for r in recipes))
    rejected = st.get("rejected") or []
    for r in hit:
        if r["title"] not in rejected:
            rejected.append(r["title"])
        if r.get("photo"):
            f = os.path.join(PHOTO_DIR, r["photo"]["file"])
            if os.path.exists(f):
                os.remove(f)
    st["recipes"] = [r for r in recipes if r not in hit]
    st["rejected"] = rejected[-KEEP_REJECTED:]
    save(st)
    print("убрано: " + ", ".join(f"«{r['title']}»" for r in hit)
          + f"; осталось {len(st['recipes'])}, в отказах {len(st['rejected'])}")


def cmd_photos_plan(a) -> None:
    """Download candidates for dishes already on the page, ready to be looked at."""
    st = load()
    recipes = st.get("recipes") or []
    if a.missing:
        recipes = [r for r in recipes
                   if not (r.get("photo")
                           and os.path.exists(os.path.join(PHOTO_DIR,
                                                           r["photo"]["file"])))]
    if not recipes:
        return
    os.makedirs(a.out, exist_ok=True)
    for r in recipes:
        query = ((r.get("photo") or {}).get("query") or r.get("photo_query")
                 or r.get("title", ""))
        print(f"\n## {r['title']}")
        for c in _download_candidates(query, a.out, a.limit):
            print(os.path.join(a.out, c))


def _download_candidates(query: str, out_dir: str, limit: int) -> list[str]:
    index_path = os.path.join(out_dir, "candidates.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    names = []
    for i, c in enumerate(ph.candidates(query, limit)):
        name = f"{re.sub(r'[^a-z0-9]+', '-', query.lower())[:28]}-{i}.jpg"
        path = os.path.join(out_dir, name)
        try:
            if not os.path.exists(path):
                ph.download(c["thumb"], path)
        except Exception as e:
            print(f"  {name}: не скачалось ({type(e).__name__})", file=sys.stderr)
            continue
        index[name] = {"file": name, "source": c.get("source", ""),
                       "title": (c.get("title") or "")[:90],
                       "credit": (c.get("artist") or "")[:80],
                       "license": c.get("license", ""), "page": c.get("page", ""),
                       "query": query}
        names.append(name)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return names


def cmd_photos_apply(a) -> None:
    """Attach the photos the model chose by eye to the dishes on the page."""
    raw = open(a.choice, encoding="utf-8").read().strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        sys.exit("в ответе про фото нет JSON-объекта")
    choice = json.loads(raw[start:end + 1])

    st = load()
    changed = 0
    for r in st.get("recipes") or []:
        f = choice.get(r["title"])
        if not isinstance(f, str) or not f.strip():
            continue
        photo = _take_chosen(f.strip(), a.candidates)
        if photo:
            r["photo"] = photo
            changed += 1
    _prune_photos(st.get("recipes") or [])
    save(st)
    print(f"фото заменено глазами: {changed}")


def _prune_photos(recipes: list) -> None:
    keep = {r["photo"]["file"] for r in recipes if r.get("photo")}
    if os.path.isdir(PHOTO_DIR):
        for f in os.listdir(PHOTO_DIR):
            if f.endswith(".jpg") and f not in keep:
                os.remove(os.path.join(PHOTO_DIR, f))


def cmd_photos_repair(a) -> None:
    """Re-fetch any dish photo whose file went missing.

    A safety net, not the main path: the file can disappear through a pruning
    bug, a half-finished deploy or a manual cleanup, and a broken image is the
    one failure the page cannot hide.
    """
    st = load()
    fixed = 0
    for r in st.get("recipes") or []:
        photo = r.get("photo")
        if photo and os.path.exists(os.path.join(PHOTO_DIR, photo["file"])):
            continue
        query = ((photo or {}).get("query") or r.get("photo_query")
                 or r.get("title", ""))
        try:
            r["photo"] = ph.fetch(query, PHOTO_DIR) if query else None
        except Exception as e:
            print(f"  {r.get('title')}: {type(e).__name__}: {e}", file=sys.stderr)
            r["photo"] = None
        fixed += 1
    if fixed:
        save(st)
    print(f"восстановлено фото: {fixed}")


def cmd_recipes_need(a) -> None:
    """How many fresh ideas are missing to fill the page."""
    st = load()
    print(max(0, WANT_RECIPES - len(st.get("recipes") or [])))


def _clean_json(text: str) -> list:
    """Models like to wrap JSON in prose or fences; take the array itself."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("в ответе нет JSON-массива")
    return json.loads(t[start:end + 1])


def _take_chosen(name: str, cand_dir: str) -> dict | None:
    """Copy a candidate the model picked by eye into dishes/, with its credit."""
    import shutil
    name = os.path.basename(name)
    src = os.path.join(cand_dir, name)
    with open(os.path.join(cand_dir, "candidates.json"), encoding="utf-8") as f:
        index = json.load(f)
    meta = index.get(name)
    if not meta or not os.path.exists(src):
        return None
    os.makedirs(PHOTO_DIR, exist_ok=True)
    out = hashlib.sha1(name.encode()).hexdigest()[:16] + ".jpg"
    shutil.copyfile(src, os.path.join(PHOTO_DIR, out))
    return {"file": out, "credit": meta["credit"], "license": meta["license"],
            "page": meta["page"], "query": meta["query"],
            "source": meta.get("source", "")}


def cmd_recipes_save(a) -> None:
    raw = open(a.file, encoding="utf-8").read()
    items = _clean_json(raw)
    if not isinstance(items, list) or not items:
        sys.exit("ожидался непустой массив рецептов")

    out = []
    for r in items[:WANT_RECIPES]:
        title = str(r.get("title", "")).strip()
        query = str(r.get("photo_query", "") or title).strip()
        if not title:
            continue
        rec_ = {
            "title": title,
            "time_min": int(r.get("time_min") or 0) or None,
            "why": str(r.get("why", "")).strip(),
            # The prompt asks for limits; trust but enforce.
            "ingredients": [str(x).strip() for x in (r.get("ingredients") or [])][:MAX_INGREDIENTS],
            "steps": [str(x).strip() for x in (r.get("steps") or [])][:MAX_STEPS],
        }
        # Photos are attached later, by a step that looks at them.
        rec_["photo"] = None
        rec_["photo_query"] = query
        out.append(rec_)

    st = load()
    if a.append:
        out = ((st.get("recipes") or []) + out)[:WANT_RECIPES]

    # Prune only AFTER merging: pruning against the new batch alone deleted the
    # photos of the dishes that were kept, leaving broken images on the page.
    _prune_photos(out)

    st["recipes"] = out
    st["recipes_ts"] = datetime.now().isoformat(timespec="seconds")
    st["recipes_model"] = a.model
    save(st)
    got = sum(1 for r in out if r.get("photo"))
    print(f"рецептов сохранено: {len(out)}, с фото: {got}")


def cmd_recipes_stale(a) -> None:
    """Exit 0 when the ideas are old enough to be worth regenerating."""
    st = load()
    ts = st.get("recipes_ts")
    if not ts or not st.get("recipes"):
        print("рецептов ещё нет")
        return
    age = (datetime.now() - datetime.fromisoformat(ts)).days
    if age < RECIPES_STALE_DAYS:
        print(f"рецептам {age} дн., ещё свежие")
        sys.exit(1)
    print(f"рецептам {age} дн.")


def cmd_sync(a) -> None:
    store = rec.sync(full=a.all)
    good = [r for r in store.values() if not r.get("unparsed")]
    print(f"чеков с позициями: {len(good)}")


def cmd_summary(a) -> None:
    st = load()
    fresh = inv.inventory()
    receipts = inv.load_receipts()
    st.update({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "date": fresh["date"],
        "proposal": fresh["proposal"],
        # The full item list is long and mostly dormant; the page only needs
        # what was actually bought recently.
        "recent": sorted((i for i in fresh["items"] if i["days_since"] <= 21),
                         key=lambda i: i["last_bought"], reverse=True),
        "stats": {
            "receipts": len(receipts),
            "products": len(fresh["items"]),
            "first": receipts[0]["ts"][:10] if receipts else None,
            "last": receipts[-1]["ts"][:10] if receipts else None,
        },
    })
    # The daily note was dropped: nobody read it and it cost a model call.
    st.pop("note", None); st.pop("notes", None)
    save(st)
    print(f"pantry.json: {len(st['proposal'])} к докупке, "
          f"{len(st['recent'])} свежих позиций, "
          f"{len(st.get('recipes') or [])} идей блюд")


def digest_text() -> str:
    st = load()
    if not st.get("generated"):
        cmd_summary(None)
        st = load()
    out = [f"Сегодня {st['date']}.",
           f"Чеков разобрано: {st['stats']['receipts']}, "
           f"период {st['stats']['first']}..{st['stats']['last']}.", ""]
    out.append("## Кончается (расчёт по частоте покупок)")
    for i in st["proposal"]:
        out.append(f"- {i['name']} (~{i['median_price']:.0f} ₽): {i['why']}")
    if not st["proposal"]:
        out.append("- ничего")
    out.append("")
    out.append("## Куплено за последние 3 недели")
    for i in st["recent"][:40]:
        amt = f", {i['est_left']:.0f}{i['unit']} по расчёту осталось" if i.get("est_left") else ""
        out.append(f"- {i['name']} — {i['last_bought']} ({i['days_since']} дн назад{amt})")
    return "\n".join(out)


def cmd_digest(a) -> None:
    print(digest_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_sync)
    sub.add_parser("summary").set_defaults(fn=cmd_summary)
    sub.add_parser("digest").set_defaults(fn=cmd_digest)
    sub.add_parser("profile").set_defaults(fn=cmd_profile)
    sub.add_parser("recipes-stale").set_defaults(fn=cmd_recipes_stale)
    sub.add_parser("recipes-need").set_defaults(fn=cmd_recipes_need)
    sub.add_parser("photos-repair").set_defaults(fn=cmd_photos_repair)
    p = sub.add_parser("photos-plan")
    p.add_argument("--out", required=True); p.add_argument("--limit", type=int, default=6)
    p.add_argument("--missing", action="store_true", help="only dishes without a photo")
    p.set_defaults(fn=cmd_photos_plan)
    p = sub.add_parser("photos-apply")
    p.add_argument("--choice", required=True); p.add_argument("--candidates", required=True)
    p.set_defaults(fn=cmd_photos_apply)
    p = sub.add_parser("reject"); p.add_argument("title")
    p.set_defaults(fn=cmd_reject)
    p = sub.add_parser("recipes-save")
    p.add_argument("--file", required=True); p.add_argument("--model", default="opus")
    p.add_argument("--append", action="store_true", help="add to the kept ones")
    p.set_defaults(fn=cmd_recipes_save)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
