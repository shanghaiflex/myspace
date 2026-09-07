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
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pantrylib import inventory as inv  # noqa: E402
from pantrylib import receipts as rec  # noqa: E402

STATE = os.path.join(ROOT, "pantry.json")
KEEP_NOTES = 10


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
        "spoiling": fresh["spoiling"],
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
          f"{len(st['spoiling'])} портится, {len(st['recent'])} свежих позиций")


def cmd_pending(a) -> None:
    """Exit 0 only when there is something new worth a note."""
    st = load()
    last_note = (st.get("note") or {}).get("basis")
    fresh = inv.inventory()
    basis = json.dumps([i["name"] for i in fresh["proposal"]] +
                       [i["name"] for i in fresh["spoiling"]], ensure_ascii=False)
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
    out.append("## Портится в ближайшие дни (оценка по категории, не из чека)")
    for i in st["spoiling"]:
        out.append(f"- {i['name']}: до {i['spoils']} "
                   f"({i['category']}, счёт как {i['spoil_basis']})")
    if not st["spoiling"]:
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
    basis = json.dumps([i["name"] for i in fresh["proposal"]] +
                       [i["name"] for i in fresh["spoiling"]], ensure_ascii=False)
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
    p = sub.add_parser("review-save")
    p.add_argument("--file", required=True); p.add_argument("--model", default="opus")
    p.set_defaults(fn=cmd_review_save)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
