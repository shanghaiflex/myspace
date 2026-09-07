#!/usr/bin/env python3
"""Turn parsed receipts into per-product purchase statistics.

The point is a defensible consumption rate for each product: how much of it
gets bought, how often, and therefore how fast it disappears. Everything the
restock logic does later leans on this, so it stays deterministic and boring.

    python3 analyze.py                 # summary
    python3 analyze.py --product творог
    python3 analyze.py --json products.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shelf_life import guess  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STORE = os.path.join(HERE, "data", "receipts.json")

MERCHANT = "Вкусвилл"


def key(name: str) -> str:
    """Collapse cosmetic variation so the same product lands on one key."""
    k = name.lower().replace("ё", "е")
    k = re.sub(r"[«»\"'()]", " ", k)
    k = re.sub(r"\b\d+(?:[.,]\d+)?\s*%", " ", k)     # fat content
    k = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:мес|г|мл|кг|л)\b", " ", k)
    k = re.sub(r"[^\w\s]", " ", k)
    return re.sub(r"\s+", " ", k).strip()


def load_receipts(merchant: str = MERCHANT) -> list[dict]:
    with open(STORE, encoding="utf-8") as f:
        store = json.load(f)
    out = [r for r in store.values()
           if not r.get("unparsed") and r.get("ts")
           and merchant.lower() in r["merchant"].lower()]
    out.sort(key=lambda r: r["ts"])
    return out


def build(receipts: list[dict]) -> dict:
    """Aggregate purchases per product key."""
    prod: dict[str, dict] = defaultdict(lambda: {
        "name": "", "buys": [], "unit": None, "sold_by": None,
        "nonfood": False, "prices": [],
    })
    for r in receipts:
        day = r["ts"][:10]
        for it in r["items"]:
            if it["nonfood"]:
                continue
            k = key(it["name"])
            if not k:
                continue
            p = prod[k]
            p["name"] = p["name"] or it["name"]
            p["unit"] = p["unit"] or it["unit"]
            p["sold_by"] = p["sold_by"] or it["sold_by"]
            p["buys"].append({"date": day, "qty": it["qty"],
                              "amount": it["amount"], "sum": it["sum"]})
            p["prices"].append(it["price"])
    return prod


def stats(prod: dict, span_days: int) -> list[dict]:
    rows = []
    for k, p in prod.items():
        days = sorted({b["date"] for b in p["buys"]})
        n = len(p["buys"])
        gaps = [(datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
                for a, b in zip(days, days[1:])]
        amounts = [b["amount"] for b in p["buys"] if b["amount"] is not None]
        # Median gap is the honest cadence: it ignores the one-off stock-up.
        cadence = statistics.median(gaps) if gaps else None
        per_buy = statistics.median(amounts) if amounts else None
        rows.append({
            "key": k,
            "name": p["name"],
            "unit": p["unit"],
            "sold_by": p["sold_by"],
            "buys": n,
            "days_seen": len(days),
            "first": days[0],
            "last": days[-1],
            "cadence_days": cadence,
            "median_per_buy": per_buy,
            # Only meaningful once we have a repeat purchase to measure against.
            "per_day": round(per_buy / cadence, 2)
            if per_buy and cadence and cadence > 0 else None,
            "median_price": statistics.median(p["prices"]),
            "regular": len(days) >= 3 and cadence is not None and cadence <= 45,
        })
    rows = [r for r in rows
            if guess(r["name"])["category"] not in ("непродукт", "не-инвентарь")]
    rows.sort(key=lambda r: (-r["buys"], r["key"]))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product")
    ap.add_argument("--json")
    ap.add_argument("--min-buys", type=int, default=3)
    a = ap.parse_args()

    receipts = load_receipts()
    if not receipts:
        sys.exit("нет разобранных чеков — сначала python3 receipts.py sync --all")
    first = datetime.fromisoformat(receipts[0]["ts"])
    last = datetime.fromisoformat(receipts[-1]["ts"])
    span = (last - first).days or 1

    rows = stats(build(receipts), span)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)

    if a.product:
        q = a.product.lower()
        for r in rows:
            if q in r["key"]:
                print(json.dumps(r, ensure_ascii=False, indent=1))
        return

    reg = [r for r in rows if r["regular"]]
    print(f"чеков: {len(receipts)}  период: {first:%Y-%m-%d}..{last:%Y-%m-%d} "
          f"({span} дней, {span/len(receipts):.1f} дн/чек)")
    print(f"уникальных товаров: {len(rows)}, регулярных (>=3 покупок, "
          f"цикл <=45 дн): {len(reg)}\n")
    print(f"{'покуп':>5} {'цикл':>5} {'за раз':>8} {'в день':>7}  товар")
    print("-" * 78)
    for r in reg[:40]:
        u = r["unit"] or r["sold_by"] or ""
        per = f"{r['median_per_buy']:.0f}{u}" if r["median_per_buy"] else "-"
        pd = f"{r['per_day']:.0f}{u}" if r["per_day"] else "-"
        cad = f"{r['cadence_days']:.0f}д" if r["cadence_days"] else "-"
        print(f"{r['buys']:>5} {cad:>5} {per:>8} {pd:>7}  {r['name'][:44]}")


if __name__ == "__main__":
    main()
