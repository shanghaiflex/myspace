#!/usr/bin/env python3
"""Траты: что сколько стоило, по чекам из «Мои чеки онлайн» (`scripts/lkdr.py`).

Считается не то, что напечатано в чеке, а то, что реально ушло со счёта: чек о передаче товара
печатают на всю сумму, но оплачен он зачётом ранее внесённого аванса, и таких чеков 153 из 1676 —
на 36,8 млн ₽ (см. `lkdr.paid()`). Продавец опознаётся по ИНН, а не по названию: одна компания
приходит в трёх-четырёх написаниях.

Три вопроса, на которые это отвечает и на которые больше ответить нечем:
  · сколько уходит в месяц и на что — по категориям из `spend_rules.json` (правится руками);
  · что повторяется каждый месяц само — подписки и регулярные платежи;
  · **персональная инфляция**: не Росстат, а твоя корзина по твоим же чекам.

  python3 scripts/spend.py summary      # то, что видит страница (JSON)
  python3 scripts/spend.py months       # по месяцам, словами
  python3 scripts/spend.py merchants [--months 12]
  python3 scripts/spend.py recurring    # что списывается регулярно
  python3 scripts/spend.py inflation    # что подорожало
  python3 scripts/spend.py unknown      # продавцы без категории — чинить в spend_rules.json
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lkdr as K  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spend_rules.json")

MONTHS_SHOWN = 24          # показываем всё, что есть после отсечки `since` (сейчас это 2025 год целиком)
RECUR_MIN = 4              # меньше четырёх списаний — ещё не регулярность
RECUR_GAP = (20, 45)       # дней между списаниями, чтобы считать это ежемесячным: счёт за ЖКУ платят
                           # не по будильнику, и полоса 24–38 его теряла
RECUR_SPAN = 100           # и тянуться это должно месяцами: три покупки одежды подряд — не подписка
RECUR_STEADY = 0.35        # разброс сумм, при котором это подписка, а не просто регулярный счёт
RECUR_SHARE = 0.6          # какая доля промежутков должна быть месячной, чтобы это была регулярность
INFL_MIN_BUYS = 6          # товар должен покупаться хотя бы столько раз
INFL_GAP_DAYS = 300        # и «тогда» с «сейчас» должны быть разнесены хотя бы на это
INFL_CATS = ("еда",)       # корзина — это магазин: у поездки на такси «цена» зависит от длины, а не от года
INFL_SKIP = re.compile(r"услуг|перевозк|принято от|вознагражд|доставк|аренд|подписк|платеж|тариф|сбор", re.I)
FORM = re.compile(r"(ОБЩЕСТВО С\s+ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ|АКЦИОНЕРНОЕ ОБЩЕСТВО|ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО"
                  r"|Индивидуальный предприниматель|ООО|ОАО|ПАО|АО|ИП|ГУП|АНО|НКО|МФК)", re.I)


def rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def title(name, inn=None, R=None):
    """Человеческое имя продавца. В чеке стоит юридическое («ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ
    "КЕХ ЕКОММЕРЦ»), и по нему не догадаешься, что это Авито, — поэтому сначала смотрим в `names`
    по ИНН, и только если там пусто, чистим юридическую форму и кавычки."""
    if inn and R:
        human = (R.get("names") or {}).get(inn)
        if human:
            return human
    n = FORM.sub(" ", re.sub(r"[«»\"]", " ", name or ""))
    n = re.sub(r"\s+", " ", n).strip(" .,-")
    return n[:1].upper() + n[1:].lower() if n.isupper() else n or "—"


def category(inn, name, R):
    c = R["byInn"].get(inn or "")
    if c:
        return c
    low = (name or "").lower()
    for pat, cat in R["byName"]:
        if re.search(pat, low):
            return cat
    return "прочее"


def rows(store=None, R=None, since=None):
    """Каждый чек, у которого двигались деньги, в нормальном виде. Зачёты аванса отброшены здесь —
    дальше их уже не существует, чтобы никто случайно не посчитал их тратой.

    Отсечка по дате — не про «давно неинтересно», а про качество данных: до 2025 года чеки рваные
    (в 2023-м их пятнадцать штук за год), и медиана месяца по ним получается не про жизнь, а про то,
    когда на кассе называли телефон."""
    store = store if store is not None else K.load(K.STORE, {})
    R = R or rules()
    since = since if since is not None else R.get("since") or ""
    big = R.get("big", 100000)
    out = []
    for v in store.values():
        money = K.paid(v)
        if money <= 0 or (since and K.when(v) < since):
            continue
        inn = (v["receipt"].get("kktOwnerInn") or "").strip()
        name = title(K.seller(v), inn, R)
        out.append({"date": K.when(v), "inn": inn, "merchant": name,
                    "category": category(inn, name, R), "sum": round(money, 2),
                    "big": money >= big, "items": K.items(v)})
    out.sort(key=lambda r: r["date"])
    return out


def months(rs, n=MONTHS_SHOWN, R=None):
    R = R or rules()
    keys = sorted({r["date"][:7] for r in rs if r["date"]})[-n:]
    index = {m: {"month": m, "total": 0.0, "big": 0.0, "n": 0, "cats": collections.Counter()} for m in keys}
    for r in rs:
        m = index.get(r["date"][:7])
        if not m:
            continue
        m["total"] += r["sum"]
        m["n"] += 1
        if r["big"]:
            m["big"] += r["sum"]
        else:
            m["cats"][r["category"]] += r["sum"]
    out = []
    for m in keys:
        d = index[m]
        out.append({"month": m, "total": round(d["total"]), "big": round(d["big"]), "n": d["n"],
                    "regular": round(d["total"] - d["big"]),
                    "cats": {k: round(v) for k, v in d["cats"].most_common()}})
    return out


def merchants(rs, since=None, limit=25, until=None):
    agg = collections.defaultdict(lambda: {"sum": 0.0, "n": 0, "name": "", "cat": "", "last": ""})
    for r in rs:
        if (since and r["date"] < since) or (until and r["date"] > until):
            continue
        a = agg[r["inn"] or r["merchant"]]
        a["sum"] += r["sum"]; a["n"] += 1
        a["name"], a["cat"] = r["merchant"], r["category"]
        a["last"] = max(a["last"], r["date"])
    out = [{"name": a["name"], "category": a["cat"], "sum": round(a["sum"]), "n": a["n"], "last": a["last"]}
           for a in agg.values()]
    return sorted(out, key=lambda a: -a["sum"])[:limit]


def recurring(rs, today=None):
    """Что списывается само каждый месяц. Смотрим последний год: подписка, о которой забыли, —
    это ровно тот случай, когда сумма маленькая, а список длинный."""
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=400)).isoformat()
    by = collections.defaultdict(list)
    for r in rs:
        if r["date"] >= since and not r["big"]:
            by[r["inn"] or r["merchant"]].append(r)
    out = []
    for group in by.values():
        group.sort(key=lambda r: r["date"])
        if len(group) < RECUR_MIN:
            continue
        days = [dt.date.fromisoformat(g["date"]).toordinal() for g in group if g["date"]]
        gaps = [b - a for a, b in zip(days, days[1:])]
        if not gaps:
            continue
        # Медианы мало: у четырёх покупок с промежутками 24, 139 и 2 дня медиана равна 24, и детская
        # одежда из Mothercare попадала в «регулярное». Требуем, чтобы в месячную полосу укладывалось
        # большинство промежутков, а не один средний.
        good = [g for g in gaps if RECUR_GAP[0] <= g <= RECUR_GAP[1]]
        if len(good) < max(3, round(RECUR_SHARE * len(gaps))):
            continue
        gap = st.median(good)
        if days[-1] - days[0] < RECUR_SPAN:
            continue          # «каждые 24 дня» на отрезке в месяц — совпадение, а не регулярность
        sums = [g["sum"] for g in group]
        mid = st.median(sums)
        last = group[-1]
        # Списания могли кончиться: PARTYstation последний раз взяли 795 ₽ в апреле, и писать про него
        # «9 360 ₽ в год» — враньё. Живым считается то, что списывали не позже двух интервалов назад.
        since_last = (today - dt.date.fromisoformat(last["date"])).days
        out.append({"name": last["merchant"], "category": last["category"], "every": round(gap),
                    "active": since_last <= gap * 2 + 7, "sinceLast": since_last,
                    "amount": round(mid), "n": len(group), "last": last["date"],
                    # подписка списывает одно и то же, счёт за коммуналку — каждый раз разное
                    "steady": bool(mid) and (max(sums) - min(sums)) / mid <= RECUR_STEADY,
                    "year": round(mid * 365 / gap)})
    return sorted(out, key=lambda x: -x["year"])


def item_key(name):
    """Одинаковый товар под разными обёртками — на один ключ. Проценты жирности, граммовка и
    маркетинговые скобки из названия выкидываются: «[M] Творог 5%, 400 г» и «Творог 9%, 200 г» — один творог."""
    k = (name or "").lower().replace("ё", "е")
    k = re.sub(r"^\[m\+?\]\s*", "", k)
    k = re.sub(r"[«»\"'()]", " ", k)
    k = re.sub(r"\b\d+(?:[.,]\d+)?\s*%", " ", k)
    k = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:мес|шт|г|мл|кг|л)\b", " ", k)
    k = re.sub(r",\s*(шт|кг)\s*$", " ", k)
    k = re.sub(r"[^\w\s]", " ", k)
    return re.sub(r"\s+", " ", k).strip()


def inflation(rs, limit=20):
    """Персональная инфляция: сравниваем медианную цену «тогда» и «сейчас» по тем товарам, которые
    покупаются регулярно. Это не индекс потребительских цен, а именно своя корзина — считать её
    больше негде, потому что нужны свои же чеки за годы."""
    buys = collections.defaultdict(list)
    for r in rs:
        if r["category"] not in INFL_CATS:
            continue
        for it in r["items"]:
            if INFL_SKIP.search(str(it.get("name") or "")):
                continue
            try:
                price = float(it.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or not r["date"]:
                continue
            buys[item_key(it.get("name"))].append((r["date"], price, str(it.get("name") or "")))
    out = []
    for key, rowsx in buys.items():
        if len(rowsx) < INFL_MIN_BUYS or not key:
            continue
        rowsx.sort()
        half = len(rowsx) // 2
        old, new = rowsx[:half], rowsx[half:]
        span = (dt.date.fromisoformat(new[-1][0]) - dt.date.fromisoformat(old[0][0])).days
        if span < INFL_GAP_DAYS:
            continue
        p_old, p_new = st.median(p for _, p, _ in old), st.median(p for _, p, _ in new)
        if p_old <= 0:
            continue
        out.append({"name": max((n for _, _, n in rowsx), key=len)[:60], "key": key, "buys": len(rowsx),
                    "was": round(p_old), "now": round(p_new), "change": round((p_new / p_old - 1) * 100),
                    "from": old[0][0], "to": new[-1][0], "years": round(span / 365, 1)})
    out.sort(key=lambda x: -x["change"])
    basket = [x for x in out if x["buys"] >= INFL_MIN_BUYS]
    median_change = round(st.median([x["change"] for x in basket]), 1) if basket else None
    return {"median": median_change, "n": len(basket),
            "up": out[:limit], "down": [x for x in out if x["change"] < 0][-limit:][::-1]}


# Строки чека, за которыми нет товара: сервисные сборы и платная доставка. Их стоит видеть отдельно —
# это единственная часть трат, которую можно убрать, ничего не потеряв.
FEE = re.compile(r"услуги сервиса|услуги курьерской|сервисный сбор|доставка курьером|стоимость доставки|"
                 r"плата за доставку|сбор за доставку", re.I)
LEAK_MIN = 3000            # мельче — не наблюдение, а шум
GROWTH = 1.5               # во столько раз должно вырасти, чтобы об этом стоило говорить


def money(x):
    return f"{round(x):,}".replace(",", " ")


def leaks(rs, today=None):
    """Наблюдения по тратам: где деньги уходят на то, что покупкой не является, и что поехало вверх.
    Ни модели, ни советов про вклады — только арифметика по своим же чекам, которую можно перепроверить
    глазами. Каждое наблюдение обязано называть число, иначе это не наблюдение.

    Правило отбора жёсткое: сюда идёт только то, на что можно повлиять, не меняя жизнь. Поэтому здесь
    нет «категория выросла в N раз» вообще: начавшаяся терапия давала «Здоровье ×17,8», и формально это
    правда, а по смыслу — не утечка, а решение человека."""
    today = today or dt.date.today()
    year, prev = str(today.year), str(today.year - 1)
    out = []

    # 1. Сборы и платная доставка — строки, за которыми нет товара. Всё считается за текущий год,
    #    включая разбивку: год назад сумма была другая, и смешивать их нельзя.
    fees_by = collections.defaultdict(lambda: [0.0, 0])
    total_fees = collections.Counter()
    for r in rs:
        for it in r["items"]:
            if not FEE.search(str(it.get("name") or "")):
                continue
            try:
                v = float(it.get("sum") or 0)
            except (TypeError, ValueError):
                continue
            total_fees[r["date"][:4]] += v
            if r["date"][:4] == year:
                e = fees_by[r["merchant"]]; e[0] += v; e[1] += 1
    if total_fees[year] >= LEAK_MIN and fees_by:
        top = max(fees_by.items(), key=lambda kv: kv[1][0])
        out.append({"kind": "fees", "title": "Сборы и платная доставка", "amount": round(total_fees[year]),
                    "note": f"за {year} год, товара за этими строками нет. Больше всего — {top[0]}: "
                            f"{money(top[1][0])} ₽ за {top[1][1]} раз, в среднем "
                            f"{money(top[1][0] / max(top[1][1], 1))} ₽"})

    # 2. Доставка еды против магазина — доля и куда она движется.
    def food(y):
        shop = sum(r["sum"] for r in rs if r["date"][:4] == y and r["category"] == "еда")
        deliv = sum(r["sum"] for r in rs if r["date"][:4] == y and r["category"] == "доставка-еды")
        return deliv, shop + deliv
    d_now, all_now = food(year)
    d_old, all_old = food(prev)
    if all_now and d_now >= LEAK_MIN:
        was = f", год назад {round(d_old / all_old * 100)}%" if all_old else ""
        out.append({"kind": "delivery", "title": "Доставка еды", "amount": round(d_now),
                    "note": f"{round(d_now / all_now * 100)}% всех денег на еду{was}"})

    # 3. Такси против метро: обе строки про одну и ту же дорогу, поэтому их сравнение честное.
    def ride(y, name):
        return sum(r["sum"] for r in rs if r["date"][:4] == y and r["merchant"] == name)
    taxi, metro = ride(year, "Яндекс.Такси"), ride(year, "Метро")
    taxi_prev = ride(prev, "Яндекс.Такси")
    if taxi >= LEAK_MIN:
        bits = [f"метро за тот же год — {money(metro)} ₽"] if metro else []
        if taxi_prev >= LEAK_MIN and taxi > taxi_prev * GROWTH:
            bits.append(f"за весь {prev} было {money(taxi_prev)} ₽")
        out.append({"kind": "taxi", "title": "Такси", "amount": round(taxi), "note": "; ".join(bits)})

    # 4. Подписки: сколько стоят живые и что перестало списываться.
    rec = recurring(rs, today)
    live = [x for x in rec if x["active"] and x["steady"]]
    if live:
        out.append({"kind": "subs", "title": "Подписки", "amount": round(sum(x["year"] for x in live)),
                    "note": "в год: " + ", ".join(f"{x['name']} {money(x['amount'])} ₽/мес" for x in live[:4])})
    for x in (x for x in rec if not x["active"]):
        out.append({"kind": "dead", "title": x["name"], "amount": round(x["amount"]),
                    "note": f"в месяц — но не списывают {x['sinceLast']} дней, последний раз {x['last']}. "
                            f"Если подписка жива, деньги вернутся в счёт"})

    # 5. Товары, подорожавшие заметно сильнее корзины: их имеет смысл пересмотреть поштучно.
    inf = inflation(rows(None, rules(), since=rules().get("sinceInflation")))
    if inf["median"] is not None:
        hot = [x for x in inf["up"] if x["change"] >= max(25, inf["median"] + 20)][:3]
        if hot:
            out.append({"kind": "prices", "title": "Подорожало сильнее корзины", "amount": None,
                        "note": "; ".join(f"{x['name'].split(',')[0][:32]} +{x['change']}%" for x in hot)
                                + f" — при том, что корзина целиком {inf['median']:+}%"})
    return sorted(out, key=lambda x: -(x["amount"] or 0))


def summary(store=None):
    R = rules()
    rs = rows(store, R)
    # У инфляции своя, более ранняя отсечка: ей нужен длинный ряд. На окне с 2025 года товаров
    # с достаточной историей остаётся 26 вместо 48, и «рост цены» начинает зависеть от того,
    # в какой половине окна случилась акция, а не от цены.
    infl_rows = rows(store, R, since=R.get("sinceInflation") or R.get("since"))
    today = dt.date.today()
    ms = months(rs, MONTHS_SHOWN, R)
    year_ago = (today - dt.timedelta(days=365)).isoformat()
    regular = [m["regular"] for m in ms[:-1]]          # текущий месяц неполный, в медиану не идёт
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "receipts": len(rs), "since": rs[0]["date"] if rs else None,
        "cut": R.get("since"),
        "titles": R["categories"],
        "months": ms,
        "median": round(st.median(regular)) if regular else None,
        "merchants": merchants(rs, year_ago),
        # Тот же список, но помесячно: «куда ушло в августе» — вопрос чаще, чем «куда ушло за год».
        "merchantsByMonth": {m["month"]: merchants(rs, m["month"] + "-01", 10, m["month"] + "-31") for m in ms},
        "recurring": recurring(rs, today),
        "inflation": dict(inflation(infl_rows), since=R.get("sinceInflation")),
        "leaks": leaks(rs),
        "big": [{"date": r["date"], "merchant": r["merchant"], "sum": round(r["sum"]),
                 "what": (r["items"][0].get("name") if r["items"] else "") or ""}
                for r in sorted((r for r in rs if r["big"]), key=lambda r: r["date"], reverse=True)[:12]],
    }


def unknown(rs, store=None):
    """Продавцы без категории. Кроме имени показываем `retailPlace` из чека — это адрес сайта продавца,
    и часто только он и объясняет, что за контора: «ООО Орбита» оказалась PARTYstation по pstv.ru."""
    store = store if store is not None else K.load(K.STORE, {})
    place = {}
    for v in store.values():
        inn = (v.get("receipt") or {}).get("kktOwnerInn") or ""
        rp = (v.get("fiscal") or {}).get("retailPlace")
        if inn and rp and inn not in place:
            place[inn] = str(rp)[:44]
    agg = collections.defaultdict(lambda: [0.0, 0, "", ""])
    for r in rs:
        if r["category"] != "прочее":
            continue
        a = agg[r["inn"] or r["merchant"]]
        a[0] += r["sum"]; a[1] += 1; a[2] = r["merchant"]; a[3] = place.get(r["inn"], "")
    return sorted(([inn] + a for inn, a in agg.items()), key=lambda a: -a[1])


def who(query, store=None, R=None):
    """Всё, что известно про продавца: имя, ИНН, сайт из чека, категория, чеки и что в них покупалось.
    Вопрос «а что это за „ООО Орбита“?» возникает постоянно, а ответ почти всегда лежит в `retailPlace` —
    там касса пишет адрес сайта. Ищем и по имени, и по ИНН, и по названию товара."""
    store = store if store is not None else K.load(K.STORE, {})
    R = R or rules()
    q = query.lower()
    hits = collections.defaultdict(list)
    for v in store.values():
        inn = (v.get("receipt") or {}).get("kktOwnerInn") or ""
        name = title(K.seller(v), inn, R)
        text = " ".join([name, inn, K.seller(v)] + [str(i.get("name") or "") for i in K.items(v)]).lower()
        if q in text:
            hits[inn or name].append(v)
    out = []
    for inn, vs in sorted(hits.items(), key=lambda kv: -sum(K.paid(v) for v in kv[1])):
        vs.sort(key=K.when)
        f = next((v.get("fiscal") or {} for v in reversed(vs) if v.get("fiscal")), {})
        out.append({"inn": inn, "name": title(K.seller(vs[-1]), inn, R), "legal": K.seller(vs[-1]),
                    "where": f.get("retailPlace"), "category": R["categories"].get(category(inn, "", R), "—"),
                    "paid": sum(K.paid(v) for v in vs), "n": len(vs),
                    "first": K.when(vs[0]), "last": K.when(vs[-1]),
                    "items": [(K.when(v), str(i.get("name") or "")[:60], i.get("sum"))
                              for v in reversed(vs) for i in K.items(v)][:12]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("summary", "months", "recurring", "inflation", "unknown", "leaks"):
        sub.add_parser(name)
    p = sub.add_parser("merchants"); p.add_argument("--months", type=int, default=12)
    p = sub.add_parser("who"); p.add_argument("query", help="имя, ИНН или слово из названия товара")
    a = ap.parse_args(argv)
    R = rules()
    rs = rows(None, R)
    if a.cmd == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
    elif a.cmd == "months":
        print(f"{'месяц':8} {'всего':>10} {'обычные':>10}   на что")
        for m in months(rs, MONTHS_SHOWN, R):
            top = ", ".join(f"{R['categories'].get(k, k).lower()} {money(v)}" for k, v in list(m["cats"].items())[:4])
            big = f"  + крупное {money(m['big'])}" if m["big"] else ""
            print(f"{m['month']:8} {money(m['total']):>10} {money(m['regular']):>10}   {top}{big}")
    elif a.cmd == "merchants":
        since = (dt.date.today() - dt.timedelta(days=31 * a.months)).isoformat()
        for x in merchants(rs, since):
            print(f"  {x['name'][:34]:36} {money(x['sum']):>10}  {x['n']:>4} чек  {R['categories'].get(x['category'], x['category'])}")
    elif a.cmd == "recurring":
        rec = recurring(rs)
        for label, want in (("Подписки (сумма не меняется)", True), ("Регулярное (сумма каждый раз своя)", False)):
            part = [x for x in rec if x["steady"] is want]
            if not part:
                continue
            print(f"\n{label}")
            print("  что                                 в месяц   раз в   за год       последний")
            for x in part:
                print(f"  {x['name'][:32]:34} {money(x['amount']):>8}  {x['every']:>3} дн  {money(x['year']):>10}   {x['last']}"
                      + ("" if x["active"] else f"  ← не списывают {x['sinceLast']} дн"))
    elif a.cmd == "inflation":
        inf = inflation(rs)
        print(f"Медианный рост цены по корзине ({inf['n']} товаров): {inf['median']:+}%\n")
        print(f"{'товар':44}{'было':>7}{'стало':>8}{'рост':>8}  покупок")
        for x in inf["up"][:15]:
            print(f"  {x['name'][:42]:44}{x['was']:>7}{x['now']:>8}{x['change']:>7}%  {x['buys']}")
        if inf["down"]:
            print("\nподешевело:")
            for x in inf["down"][:5]:
                print(f"  {x['name'][:42]:44}{x['was']:>7}{x['now']:>8}{x['change']:>7}%  {x['buys']}")
    elif a.cmd == "leaks":
        for x in leaks(rs):
            head = f"{x['title']}" + (f" — {money(x['amount'])} ₽" if x["amount"] is not None else "")
            print(f"  {head}\n      {x['note']}")
    elif a.cmd == "who":
        found = who(a.query, None, R)
        if not found:
            print("никого не нашёл")
        for x in found[:5]:
            print(f"\n{x['name']}  ({x['category']})")
            print(f"  ИНН {x['inn']} · {x['legal'][:54]}")
            print(f"  сайт из чека: {x['where'] or '—'}")
            print(f"  {x['n']} чеков на {money(x['paid'])} ₽, {x['first']} … {x['last']}")
            for d, n, sm in x["items"]:
                print(f"    {d}  {n:<62} {money(sm or 0):>9} ₽")
    elif a.cmd == "unknown":
        print("Продавцы без категории (чинить в spend_rules.json):")
        for inn, s, n, name, where in unknown(rs):
            print(f"  {inn:<14} {money(s):>10} {n:>4} чек  {name[:30]:32} {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
