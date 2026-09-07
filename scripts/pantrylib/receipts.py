#!/usr/bin/env python3
"""Parse OFD fiscal receipts out of Gmail into structured JSON.

    python3 receipts.py sync            # incremental: fetch what we don't have yet
    python3 receipts.py sync --all      # full history re-fetch
    python3 receipts.py stats

Receipts land in data/receipts.json, keyed by IMAP uid.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gmail_tool import Mailbox  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STORE = os.path.join(DATA, "receipts.json")

OFD_SENDERS = ["1-ofd.ru", "chek.pofd.ru", "ofd.ru", "beeline.ru",
               "taxcom.ru", "ofd-initpro.ru", "ofd.yandex.ru"]

# "1. \t Сырники, 210 г,шт \t 174.00 \t 1 \t 174.00"
LINE = re.compile(
    r"^\s*(\d+)\.\s*\t\s*(.+?)\s*\t\s*([\d.,]+)\s*\t\s*([\d.,]+)\s*\t\s*([\d.,]+)\s*$", re.M)
TOTAL = re.compile(r"ИТОГО:\s*\t\s*([\d.,]+)")
WHEN = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|г|мл|л)\b", re.I)
# trailing unit of sale: "…,шт" or "…,кг"
SOLD_BY = re.compile(r",\s*(шт|кг)\s*$", re.I)
NONFOOD = re.compile(r"пакет|майка|пакет-майка", re.I)

MULT = {"кг": 1000.0, "г": 1.0, "л": 1000.0, "мл": 1.0}


def _num(s: str) -> float:
    return float(s.replace(",", ".").replace(" ", ""))


def parse_item(raw_name: str, qty: float) -> dict:
    """Split a receipt line's product name into base name + real quantity.

    Three shapes occur in the wild:
      "Дыня Колхозница,кг"           -> sold by weight, qty IS kilograms
      "Сметана 15%, 200 г,шт"        -> pieces, size lives in the name
      "Бекон сырокопченый, нарезка,шт" -> pieces, size unknown
    """
    name = raw_name.strip()
    sold = SOLD_BY.search(name)
    sold_by = sold.group(1).lower() if sold else "шт"
    name = SOLD_BY.sub("", name).strip()
    name = re.sub(r"^\[M\+?\]\s*", "", name).strip()  # loyalty marker

    size = None
    if sold_by == "кг":
        amount, unit = qty * 1000.0, "г"  # quantity column is the weight
    else:
        m = None
        for m in SIZE.finditer(name):
            pass  # keep the last match: "Сыр 45% ... , 90 г"
        if m:
            size = _num(m.group(1))
            u = m.group(2).lower()
            unit = "мл" if u in ("мл", "л") else "г"
            amount = size * MULT[u] * qty
            name = name[:m.start()].rstrip(" ,.").strip()
        else:
            amount, unit = None, None

    return {
        "raw": raw_name.strip(),
        "name": re.sub(r"\s*,\s*$", "", name).strip(),
        "sold_by": sold_by,
        "pack_size": size,
        "qty": qty,
        "amount": round(amount, 1) if amount is not None else None,
        "unit": unit,
        "nonfood": bool(NONFOOD.search(raw_name)),
    }


# --- Second template (OFD.RU). Same receipt, wholly different layout:
#       Наггетсы куриные, 500 г
#       1 X 314.00
#       = 314.00
#       Мера кол-ва предмета расчета
#       шт.
QTYLINE = re.compile(r"^([\d]+(?:[.,]\d+)?)\s*X\s*([\d.,]+)$")
SUMLINE = re.compile(r"^=\s*([\d.,]+)$")
TOTAL2 = re.compile(r"^ИТОГ$", re.M)
WHEN2 = re.compile(r"ДАТА ВЫДАЧИ\s*\n\s*(\d{2}\.\d{2}\.\d{2})\s+(\d{2}:\d{2})")
SKIP_NAME = re.compile(r"^(в т\.ч\.|ПРИЗНАК|Размер НДС|Мера кол|Код товара|"
                       r"Результат проверки|\[M\+?\]|=)", re.I)


def parse_ofdru(body: str) -> tuple[list[dict], float] | None:
    lines = [l.strip() for l in body.split("\n")]
    rows: list[dict] = []
    for i, line in enumerate(lines):
        m = QTYLINE.match(line)
        if not m:
            continue
        name = ""
        for j in range(i - 1, max(i - 4, -1), -1):
            if lines[j] and not SKIP_NAME.match(lines[j]):
                name = lines[j]
                break
        if not name:
            continue
        total = None
        for j in range(i + 1, min(i + 8, len(lines))):
            sm = SUMLINE.match(lines[j])
            if sm:
                total = _num(sm.group(1))
                break
        unit = "шт"
        for j in range(i + 1, min(i + 30, len(lines))):
            if lines[j].startswith("Мера кол"):
                nxt = next((x for x in lines[j + 1:j + 4] if x), "шт")
                unit = "кг" if "кг" in nxt else "шт"
                break
        it = parse_item(f"{name},{unit}", _num(m.group(1)))
        it["price"] = _num(m.group(2))
        it["sum"] = total if total is not None else it["price"] * _num(m.group(1))
        rows.append(it)
    if not rows:
        return None
    tm = TOTAL2.search(body)
    grand = None
    if tm:
        tail = body[tm.end():].strip().split("\n")
        if tail:
            try:
                grand = _num(tail[0])
            except ValueError:
                grand = None
    return rows, grand if grand is not None else sum(r["sum"] for r in rows)


def parse_receipt(msg: dict) -> dict | None:
    body = msg["body"]
    rows = LINE.findall(body)
    tot = TOTAL.search(body)
    if not rows or not tot:
        alt = parse_ofdru(body)
        if alt is None:
            return None
        items, grand = alt
        when = WHEN2.search(body)
        ts = None
        if when:
            try:
                ts = datetime.strptime(f"{when.group(1)} {when.group(2)}",
                                       "%d.%m.%y %H:%M").isoformat()
            except ValueError:
                pass
        mm = re.search(r'(АКЦИОНЕРНОЕ ОБЩЕСТВО|ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ|'
                       r'АО|ООО|ПАО|ИП)\s+"?([^"\n]{2,60})"?', body)
        merchant = f"{mm.group(1)} {mm.group(2)}".strip().strip('"') if mm else "unknown"
        return {"uid": msg["uid"], "ts": ts, "merchant": merchant,
                "total": grand, "items": items}

    when = WHEN.search(msg["subject"]) or WHEN.search(body)
    ts = None
    if when:
        try:
            ts = datetime.strptime(f"{when.group(1)} {when.group(2)}",
                                   "%d.%m.%Y %H:%M").isoformat()
        except ValueError:
            pass

    merchant = "unknown"
    mm = re.search(r'(АО|ООО|ОАО|ПАО|ИП)\s+"?([^"\n]{2,60})"?', body)
    if mm:
        merchant = f"{mm.group(1)} {mm.group(2)}".strip().strip('"')

    items = [parse_item(r[1], _num(r[3])) for r in rows]
    for it, r in zip(items, rows):
        it["price"] = _num(r[2])
        it["sum"] = _num(r[4])

    return {
        "uid": msg["uid"],
        "ts": ts,
        "merchant": merchant,
        "total": _num(tot.group(1)),
        "items": items,
    }


def load() -> dict:
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(store: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def sync(full: bool = False, window: str = "newer_than:400d") -> dict:
    store = load()
    q = "(" + " OR ".join(f"from:{s}" for s in OFD_SENDERS) + ")"
    if not full:
        q += " " + window

    mb = Mailbox()
    try:
        uids = [u.decode() for u in mb.search(q, limit=5000)]
        # retry anything a previous run could not read: parsers improve
        todo = [u for u in uids if u not in store or store[u].get("unparsed")]
        print(f"писем: {len(uids)}, новых: {len(todo)}", file=sys.stderr)
        added = skipped = 0
        for u in todo:
            try:
                msg = mb.body(u, max_chars=80000)
            except Exception as e:
                print(f"  uid={u}: {e}", file=sys.stderr)
                continue
            rec = parse_receipt(msg)
            if rec is None:
                store[u] = {"uid": u, "unparsed": True,
                            "subject": msg["subject"][:90]}
                skipped += 1
                continue
            store[u] = rec
            added += 1
    finally:
        mb.close()
    save(store)
    print(f"разобрано: {added}, без позиций: {skipped}", file=sys.stderr)
    return store


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--all", action="store_true")
    sub.add_parser("stats")
    a = ap.parse_args()

    store = sync(full=a.all) if a.cmd == "sync" else load()

    good = [r for r in store.values() if not r.get("unparsed")]
    bad = [r for r in store.values() if r.get("unparsed")]
    by_merchant: dict[str, int] = {}
    for r in good:
        by_merchant[r["merchant"]] = by_merchant.get(r["merchant"], 0) + 1
    print(json.dumps({
        "чеков_с_позициями": len(good),
        "писем_без_позиций": len(bad),
        "позиций": sum(len(r["items"]) for r in good),
        "магазины": dict(sorted(by_merchant.items(), key=lambda kv: -kv[1])[:8]),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
