#!/usr/bin/env python3
"""Инвест: стратегия из ~/workspace/invest на этой машине (на mini — ~/invest) для страницы invest.html.

Источник — файлы, которые пишет прогон стратегии (`scripts/daily.sh` → `invest plan --execute`), сам этот скрипт
в T-Invest не ходит и токенов не знает:
  data/forward.csv        стоимость счёта после каждого прогона (time, phase, account, value, orders)
  data/orders.csv         каждая заявка (time, mode, account, ticker, lots, filled, price, amount, status)
  data/state-<mode>.json  снимок после прогона: позиции, дивидендный план, ротация, что исполнено и что нет
  data/keyrate.csv        ключевая ставка ЦБ — для сравнения «а если бы лежало под ключевую»
  ~/.config/tinvest/mode  режим прогона (sandbox | real | both), ~/.config/tinvest/STOP — рубильник

Советов здесь нет, как и на «Тратах»: только то, что стратегия сделала и собирается сделать по своим правилам.

Usage:
  invest.py summary         то, что отдаёт /api/invest
"""
import csv
import datetime as dt
import json
import os
import sys

HOME = os.path.expanduser("~")
DIR = os.environ.get("INVEST_DIR") or next(
    (p for p in (os.path.join(HOME, "invest"), os.path.join(HOME, "workspace", "invest")) if os.path.isdir(p)), "")
CONF = os.path.join(HOME, ".config", "tinvest")
KINDS = ("real", "sandbox")
ORDERS_SHOWN = 40
SCHEDULE = [("07:05", "утро", "продажи по выходу из разгона и откуп шорта фьючерса"),
            ("18:15", "вечер", "обновление данных"),
            ("18:30", "вечер", "покупки, шорт IMOEXF на исполненное, свободное — в LQDT")]


def _csv(name):
    try:
        with open(os.path.join(DIR, "data", name), encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def mode():
    try:
        with open(os.path.join(CONF, "mode"), encoding="utf-8") as f:
            m = f.read().strip()
    except OSError:
        m = ""
    return m if m in ("real", "both") else "sandbox"


def keyrate_growth(start, end, rates):
    """Во сколько раз выросли бы деньги под ключевую ставку с даты start по end (простые дневные начисления)."""
    if not rates:
        return None
    k, rate, i = 1.0, rates[0][1], 0
    d = start
    while d < end:
        while i < len(rates) and rates[i][0] <= d:
            rate = rates[i][1]
            i += 1
        k *= 1 + rate / 365
        d += dt.timedelta(days=1)
    return k


def account(kind, acc_id, forward, orders, rates):
    rows = [r for r in forward if r["account"] == acc_id]
    state = _json(os.path.join(DIR, "data", f"state-{kind}.json"))
    if state and state.get("account") != acc_id:
        state = None
    series = [[r["time"], float(r["value"])] for r in rows]
    out = {"kind": kind, "account": acc_id, "name": (state or {}).get("name"), "series": series, "state": state,
           "orders": [o for o in orders if o["account"] == acc_id][-ORDERS_SHOWN:][::-1]}
    if series:
        first, last = series[0], series[-1]
        t0 = dt.datetime.strptime(first[0], "%Y-%m-%d %H:%M").date()
        t1 = dt.datetime.strptime(last[0], "%Y-%m-%d %H:%M").date()
        g = keyrate_growth(t0, max(t1, t0), rates)
        out.update(start={"at": first[0], "value": first[1]}, value=last[1], at=last[0],
                   pnl=last[1] - first[1], pnlPct=(last[1] / first[1] - 1) * 100 if first[1] else None,
                   keyrate=first[1] * g if g else None)
    return out


def summary():
    forward, orders = _csv("forward.csv"), _csv("orders.csv")
    rates = []
    for r in _csv("keyrate.csv"):
        try:
            rates.append((dt.date.fromisoformat(r["date"]), float(r["rate"])))
        except (KeyError, ValueError):
            pass
    # какой счёт чей: снимок прогона знает точно, журнал заявок — по колонке mode (до первого снимка)
    ids = {}
    for kind in KINDS:
        s = _json(os.path.join(DIR, "data", f"state-{kind}.json"))
        if s and s.get("account"):
            ids[kind] = s["account"]
    for o in orders:
        if o.get("mode") in KINDS:
            ids.setdefault(o["mode"], o["account"])
    m = mode()
    active = {"real": m in ("real", "both"), "sandbox": m in ("sandbox", "both")}
    accounts = []
    for kind in KINDS:
        # песочница — только пока она в прогоне: выключенная остаётся в данных, но на странице не нужна
        if kind in ids and (active[kind] or kind == "real"):
            a = account(kind, ids[kind], forward, orders, rates)
            a["active"] = active[kind]
            accounts.append(a)
    return {"mode": m, "stop": os.path.exists(os.path.join(CONF, "STOP")), "found": bool(DIR),
            "keyrate": rates[-1][1] if rates else None, "schedule": [dict(zip(("time", "phase", "what"), s)) for s in SCHEDULE],
            "accounts": accounts}


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["summary"]):
        sys.exit(__doc__)
    print(json.dumps(summary(), ensure_ascii=False, indent=1))
