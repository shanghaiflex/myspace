#!/usr/bin/env python3
"""Inventory and a deliberately conservative restock proposal.

Receipts say what came in; nothing says what went out. So consumption is
inferred from how often a product gets rebought, and the whole thing is built
to under-suggest: a wrong "you're out of milk" costs a wasted trip, a missing
suggestion costs nothing the user can't see for themselves.

    python3 pantry.py                     # inventory + proposal
    python3 pantry.py --json state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, HERE)

from analyze import build, key, load_receipts, stats  # noqa: E402
from shelf_life import guess  # noqa: E402

# --- restock thresholds. Tuned toward silence, not helpfulness. ---
MIN_BUYS = 3          # never propose something bought once or twice
MAX_CADENCE = 45      # a >45-day cycle is not a staple
DORMANT_FACTOR = 1.8  # last buy older than cadence*this => dropped from the diet
SEASON_MIN_BUYS = 5   # below this there is not enough history to call it seasonal
DUE_FACTOR = 0.8      # do not nag before 80% of the usual cycle has passed
COOLDOWN_DAYS = 2     # never propose within 2 days of a purchase
HORIZON_DAYS = 3      # "running out" means: gone within 3 days


def months_bought(receipts: list[dict]) -> dict[str, set[int]]:
    """Which calendar months each product has ever been bought in.

    Watermelon and new potatoes are bought every 10 days -- in July. In
    September their cadence is not "overdue", the season is simply over.
    """
    seen: dict[str, set[int]] = {}
    for r in receipts:
        month = int(r["ts"][5:7])
        for it in r["items"]:
            seen.setdefault(key(it["name"]), set()).add(month)
    return seen


def in_season(months: set[int], today: date) -> bool:
    """True if this product has ever been bought around this time of year."""
    window = {(today.month - 2 + i) % 12 + 1 for i in range(3)}  # +-1 month
    return bool(months & window)


def inventory(today: date | None = None) -> dict:
    today = today or date.today()
    receipts = load_receipts()
    if not receipts:
        sys.exit("нет разобранных чеков — сначала python3 receipts.py sync --all")

    seasons = months_bought(receipts)
    rows = stats(build(receipts), 0)
    items, proposal = [], []

    for r in rows:
        sl = guess(r["name"])
        if sl["category"] in ("непродукт", "не-инвентарь"):
            continue
        last = datetime.fromisoformat(r["last"]).date()
        age = (today - last).days
        cad = r["cadence_days"]
        per_day = r["per_day"]

        # Consumed within its own shelf life => treat as opened on purchase day.
        opened_style = cad is not None and cad <= sl["closed"]
        spoil_days = sl["opened"] if opened_style else sl["closed"]
        spoil_on = last + timedelta(days=spoil_days)

        left = None
        run_out = None
        if per_day and r["median_per_buy"]:
            left = max(r["median_per_buy"] - per_day * age, 0)
            run_out = last + timedelta(days=r["median_per_buy"] / per_day)

        item = {
            "name": r["name"], "category": sl["category"], "unit": r["unit"],
            "buys": r["buys"], "last_bought": r["last"], "days_since": age,
            "cadence_days": cad, "per_day": per_day,
            "est_left": round(left, 1) if left is not None else None,
            "runs_out": run_out.isoformat() if run_out else None,
            "spoils": spoil_on.isoformat(),
            "spoil_basis": "вскрыто" if opened_style else "закрыто",
            "median_price": r["median_price"],
        }
        items.append(item)

        # ---- should we suggest rebuying it? ----
        if r["buys"] < MIN_BUYS or not cad or cad > MAX_CADENCE:
            continue
        if age > cad * DORMANT_FACTOR:
            continue                      # fell out of the diet
        if age < COOLDOWN_DAYS or age < cad * DUE_FACTOR:
            continue                      # too soon to nag
        gone_by = run_out or (last + timedelta(days=cad))
        if (gone_by - today).days > HORIZON_DAYS:
            continue                      # still has stock
        overdue = (today - gone_by).days
        if overdue > cad:
            continue                      # a whole cycle late: not wanted, not forgotten
        months = seasons.get(r["key"], set())
        if r["buys"] >= SEASON_MIN_BUYS and not in_season(months, today):
            continue                      # out of season
        item = dict(item)
        item["why"] = (f"берёшь раз в {cad:.0f} дн., прошло {age}; "
                       f"по расчёту кончилось {gone_by:%d.%m}")
        item["overdue_days"] = overdue
        proposal.append(item)

    proposal.sort(key=lambda i: (-i["overdue_days"], i["name"]))
    spoiling = sorted(
        (i for i in items
         if 0 <= (date.fromisoformat(i["spoils"]) - today).days <= 3
         and i["days_since"] <= 14),
        key=lambda i: i["spoils"])

    return {"date": today.isoformat(), "items": items,
            "proposal": proposal, "spoiling": spoiling}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--date", help="считать на эту дату (YYYY-MM-DD)")
    a = ap.parse_args()

    st = inventory(date.fromisoformat(a.date) if a.date else None)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)

    print(f"на {st['date']}: товаров в модели {len(st['items'])}\n")
    print(f"=== ДОКУПИТЬ ({len(st['proposal'])}) ===")
    for i in st["proposal"]:
        print(f"  {i['name'][:40]:<40} ~{i['median_price']:.0f}₽  {i['why']}")
    if not st["proposal"]:
        print("  ничего")
    print(f"\n=== ПОРТИТСЯ В БЛИЖАЙШИЕ 3 ДНЯ ({len(st['spoiling'])}) ===")
    for i in st["spoiling"]:
        print(f"  {i['name'][:40]:<40} до {i['spoils']} ({i['category']}, {i['spoil_basis']})")
    if not st["spoiling"]:
        print("  ничего")


if __name__ == "__main__":
    main()
