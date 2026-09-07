#!/usr/bin/env python3
"""Guess a shelf life for a product name.

Nothing in a fiscal receipt says how long food keeps, so this is inference:
keyword -> category -> days. The table in data/shelf_life.json is meant to be
corrected by hand as reality disagrees with it; every guess carries the rule
that produced it so a wrong answer is traceable to one line.
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(HERE, "data", "shelf_life.json")

_cache: dict | None = None


def table() -> dict:
    global _cache
    if _cache is None:
        with open(TABLE, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def guess(name: str) -> dict:
    """Return {category, closed, opened, matched} for a product name."""
    n = " " + name.lower().replace("ё", "е") + " "
    n = re.sub(r"\s+", " ", n)
    for cat in table()["categories"]:
        for kw in cat["keywords"]:
            if kw in n:
                return {"category": cat["id"], "closed": cat["closed"],
                        "opened": cat["opened"], "matched": kw}
    d = table()["default"]
    return {"category": d["id"], "closed": d["closed"], "opened": d["opened"],
            "matched": None}


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(arg, "->", json.dumps(guess(arg), ensure_ascii=False))
