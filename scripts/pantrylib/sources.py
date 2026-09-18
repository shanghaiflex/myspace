#!/usr/bin/env python3
"""Два источника чеков в один список.

Почта (`receipts.py`) видит только то, что ОФД прислал письмом; личный кабинет ФНС (`scripts/lkdr.py`) —
всё, где на кассе назвали телефон или почту. По ВкусВиллу они пересекаются, но ни один не покрывает
другой: 386 чеков общих, 84 есть только в почте (в основном 2022 и 2024 годы), 29 — только в кабинете.
Поэтому читаются оба, а совпадения схлопываются по паре «дата + сумма».

Здесь же чинится двойной счёт, которого в почтовом конвейере не было видно: чек о передаче товара
печатается на всю сумму, но оплачен зачётом ранее внесённого аванса (`lkdr.offset()`), и доставка
ВкусВилла приходила бы в статистику дважды — товар считался бы съеденным вдвое быстрее.
"""
from __future__ import annotations

from collections import Counter

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from receipts import STORE as MAIL_STORE, parse_item  # noqa: E402

# ИНН продуктовых, которые имеет смысл читать как «еду»: ВкусВилл и Сбермаркет (Инстамарт).
FOOD_INNS = {"7734443270": "Вкусвилл", "9705118142": "Сбермаркет"}


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def mail_receipts() -> list[dict]:
    return [r for r in _load(MAIL_STORE).values() if not r.get("unparsed") and r.get("ts")]


def lkdr_receipts() -> list[dict]:
    """Чеки из кабинета ФНС в том же виде, в каком их отдаёт почтовый парсер."""
    import lkdr as K
    out = []
    for key, v in _load(K.STORE).items():
        inn = (v.get("receipt") or {}).get("kktOwnerInn") or ""
        if inn not in FOOD_INNS or K.offset(v):
            continue
        f = v.get("fiscal") or {}
        ts = (f.get("dateTime") or (v["receipt"].get("createdDate") or ""))[:19]
        if not ts:
            continue
        items = []
        for it in f.get("items") or []:
            try:
                qty = float(it.get("quantity") or 1)
                price = float(it.get("price") or 0)
                total = float(it.get("sum") or price * qty)
            except (TypeError, ValueError):
                continue
            row = parse_item(str(it.get("name") or ""), qty)
            row["price"], row["sum"] = price, total
            items.append(row)
        if not items:
            continue
        out.append({"uid": "lkdr:" + key, "ts": ts, "merchant": FOOD_INNS[inn],
                    "total": K.paid(v) or K.total(v), "items": items, "source": "lkdr"})
    return out


def _sig(r):
    """Один и тот же поход в магазин глазами двух источников: дата и сумма до копейки."""
    try:
        return r["ts"][:10], round(float(r.get("total") or 0), 2)
    except (TypeError, ValueError):
        return r["ts"][:10], 0.0


def receipts(merchant: str | None = None) -> list[dict]:
    """Оба источника, без дублей. Почта считается основной (её парсер обкатан), кабинет добавляет
    то, чего в письмах не было.

    Схлопывание идёт **один к одному**, а не по множеству: внутри одной только почты 46 пар чеков
    имеют одинаковые «дата + сумма» (два похода в магазин в один день на одну сумму — обычное дело),
    и если бы подпись просто лежала в set, второй такой чек из кабинета пропал бы. Время в ключ не
    берётся: у 7 из 386 совпавших пар оно расходится на пару минут, а у одной — на два часа.
    """
    out = mail_receipts()
    left = Counter(_sig(r) for r in out)
    for r in lkdr_receipts():
        sig = _sig(r)
        if left.get(sig):
            left[sig] -= 1          # этот чек в почте уже есть — гасим ровно одну копию
            continue
        out.append(r)
    if merchant:
        out = [r for r in out if merchant.lower() in (r.get("merchant") or "").lower()]
    out.sort(key=lambda r: r["ts"])
    return out


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else None
    mail, lk, both = mail_receipts(), lkdr_receipts(), receipts(m)
    print(f"почта {len(mail)}, кабинет {len(lk)}, вместе без дублей {len(both)}"
          + (f" (по «{m}»)" if m else ""))
    if both:
        print(f"период {both[0]['ts'][:10]} — {both[-1]['ts'][:10]}, позиций {sum(len(r['items']) for r in both)}")
