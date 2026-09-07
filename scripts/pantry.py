#!/usr/bin/env python3
"""Pantry CLI: keep pantry.json fresh and feed the daily note to Claude.

    python3 scripts/pantry.py sync            # pull new receipts from Gmail
    python3 scripts/pantry.py summary         # recompute stock -> pantry.json
    python3 scripts/pantry.py pending         # exit 1 if nothing changed since the last note
    python3 scripts/pantry.py digest          # plain-text context for the note
    python3 scripts/pantry.py review-save --file note.txt --model opus

Mirrors scripts/health.py: the pipeline is deterministic, the model only writes
the prose at the end.
"""

from __future__ import annotations

import argparse
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
KEEP_NOTES = 10
MAX_INGREDIENTS = 6
MAX_STEPS = 4
RECIPES_STALE_DAYS = 5


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
    out.append("")
    out.append("## Что я покупаю регулярно")
    for r in [x for x in rows if x["regular"]][:35]:
        out.append(f"- {r['name']} (раз в {r['cadence_days']:.0f} дн.)")
    return "\n".join(out)


def cmd_profile(a) -> None:
    print(profile_text())


def _clean_json(text: str) -> list:
    """Models like to wrap JSON in prose or fences; take the array itself."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("в ответе нет JSON-массива")
    return json.loads(t[start:end + 1])


def cmd_recipes_save(a) -> None:
    raw = open(a.file, encoding="utf-8").read()
    items = _clean_json(raw)
    if not isinstance(items, list) or not items:
        sys.exit("ожидался непустой массив рецептов")

    out = []
    for r in items[:4]:
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
        try:
            rec_["photo"] = ph.fetch(query, PHOTO_DIR)
        except Exception as e:                       # a missing photo is not fatal
            print(f"  фото для «{title}» не вышло: {type(e).__name__}: {e}",
                  file=sys.stderr)
            rec_["photo"] = None
        out.append(rec_)

    st = load()
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
    save(st)
    print(f"pantry.json: {len(st['proposal'])} к докупке, "
          f"{len(st['recent'])} свежих позиций, "
          f"{len(st.get('recipes') or [])} идей блюд")


def cmd_pending(a) -> None:
    """Exit 0 only when there is something new worth a note."""
    st = load()
    last_note = (st.get("note") or {}).get("basis")
    fresh = inv.inventory()
    basis = json.dumps([i["name"] for i in fresh["proposal"]], ensure_ascii=False)
    if basis == last_note:
        print("ничего не изменилось с прошлой заметки")
        sys.exit(1)
    print(basis)


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


def cmd_review_save(a) -> None:
    st = load()
    text = open(a.file, encoding="utf-8").read().strip()
    if not text:
        sys.exit("пустая заметка, не сохраняю")
    fresh = inv.inventory()
    basis = json.dumps([i["name"] for i in fresh["proposal"]], ensure_ascii=False)
    note = {"ts": datetime.now().isoformat(timespec="seconds"),
            "model": a.model, "text": text, "basis": basis}
    st["note"] = note
    st["notes"] = ([note] + st.get("notes", []))[:KEEP_NOTES]
    save(st)
    print("заметка сохранена")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_sync)
    sub.add_parser("summary").set_defaults(fn=cmd_summary)
    sub.add_parser("pending").set_defaults(fn=cmd_pending)
    sub.add_parser("digest").set_defaults(fn=cmd_digest)
    sub.add_parser("profile").set_defaults(fn=cmd_profile)
    sub.add_parser("recipes-stale").set_defaults(fn=cmd_recipes_stale)
    p = sub.add_parser("recipes-save")
    p.add_argument("--file", required=True); p.add_argument("--model", default="opus")
    p.set_defaults(fn=cmd_recipes_save)
    p = sub.add_parser("review-save")
    p.add_argument("--file", required=True); p.add_argument("--model", default="opus")
    p.set_defaults(fn=cmd_review_save)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
