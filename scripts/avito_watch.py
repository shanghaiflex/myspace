#!/usr/bin/env python3
"""Слежка за Авито: одна и та же выдача каждый день, новые карточки — по жёстким правилам, звонок только по делу.

Неделями шло одно и то же: «как сегодня дела?» → выдача → карточки → «а ты все карточки смотрел, не только первую
страницу?». Здесь это делает mini раз в день, а человеку приходит уведомление только о том, что прошло правила,
или о подешевевшем подходящем, — и в каждом сказано, сколько просмотрено.

Слежка — файл `avito/watches/<id>.json` (поиск, правила, размер, город/доставка). Слежки и состояние — что
видели, по какой цене, что решили по карточке, отчёты прогонов (`data/avito-watch.json`) — живут **только на
mini**: репозиторий публичный, а покупки — личное (оба пути в .gitignore и в исключениях deploy.sh). На ноутбуке
любая команда сама уходит на mini по ssh, так что из чата это одна строка: `avito_watch.py stop iphone-17-pro`.

Прогон (`run`, launchd cc.bodywithoutorgans.avitowatch, deploy/install-avito-watch.sh):
  1. Видимое окно Chrome for Testing на mini со своим профилем (`~/.avito-watch/profile`, порт 9222), через
     `av.py` скилла avito. Авито идёт мимо VPN с домашнего IP (deploy/direct-routes.txt), того же, что у
     ноутбука: каждый лишний запрос выжигает адрес и пользователю тоже. Капчу решает человек, не скрипт.
  2. Выдача по дате (`s=104`), страница за страницей; дальше не идём, когда страница целиком знакомая.
  3. Новые объявления → отсев по заголовку и цене (карточки — дефицит: Qrator режет их раньше выдачи) →
     карточка навигацией, не больше `max_cards` за прогон, минута между ними; не успели — очередь на завтра.
  4. В коде: снято/в резерве, число отзывов продавца, город. Остальные правила и фото — `claude -p` (avito/JUDGE.md)
     с одним инструментом Read на папку с фото; сколько фото он действительно открыл, считаем по его вызовам.
  5. Уведомление (notify.py → /api/updates) — одно на прогон и только если есть подходящие или подешевевшие
     подходящие; блок или капча — тоже одно уведомление в день, а не тишина. Охват — в каждом.
Прогон ставится несколько раз в день, но работает один раз: следующие запуски — только если сегодняшний
упёрся в блок, капчу или очередь (`due`).

Usage:
  avito_watch.py list                        слежки и последний прогон
  avito_watch.py show <id>                   файл слежки, отчёт, подходящие
  avito_watch.py add <id> --title T --url U [--url U2] --rule R [--rule R2] [--price-min N] [--price-max N]
                 [--title-must RE] [--title-not RE] [--size S] [--region Москва] [--delivery] [--min-reviews 1]
                 [--pages 3] [--max-cards 12] [--replace]
  avito_watch.py stop <id> | start <id>
  avito_watch.py run [--watch id] [--force] [--max-cards N]      (на mini; через scripts/avito_watch.sh)
  avito_watch.py due                         код 0, если сегодня есть что делать
  avito_watch.py status                      то, что отдаёт /api/avito
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHES = os.path.join(ROOT, "avito", "watches")
STATE = os.path.join(ROOT, "data", "avito-watch.json")
JUDGE = os.path.join(ROOT, "avito", "JUDGE.md")
WORK = os.path.expanduser(os.environ.get("AVITO_WATCH_WORK") or "~/.avito-watch")
AV_DIR = os.path.expanduser("~/.claude/skills/avito")
MINI = os.environ.get("MINI_HOST", "mini")

CARD_PAUSE = (60, 90)       # секунд между карточками: так 03.10.2026 прошли три подряд (скилл avito)
PAGE_PAUSE = (8, 15)        # между страницами выдачи
MAX_PHOTOS = 12
JUDGE_MODEL = os.environ.get("AVITO_JUDGE_MODEL", "opus")
EVENTS_KEEP_DAYS = 14
MATCHES_SHOWN = 6


def now():
    return dt.datetime.now().astimezone()


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


# ------------------------------------------------------------------ файлы
def load_watches():
    out = []
    for name in sorted(os.listdir(WATCHES)) if os.path.isdir(WATCHES) else []:
        if name.endswith(".json"):
            with open(os.path.join(WATCHES, name), encoding="utf-8") as f:
                w = json.load(f)
            w.setdefault("id", name[:-5])
            out.append(w)
    return out


def watch_path(wid):
    return os.path.join(WATCHES, f"{wid}.json")


def save_watch(w):
    os.makedirs(WATCHES, exist_ok=True)
    with open(watch_path(w["id"]), "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"watches": {}, "events": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    cut = iso(now() - dt.timedelta(days=EVENTS_KEEP_DAYS))
    st["events"] = [e for e in st.get("events", []) if e["at"] >= cut]
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)   # serve.py читает файл в любой момент


def wstate(st, wid):
    return st["watches"].setdefault(wid, {"seen": {}, "queue": [], "runs": []})


# ------------------------------------------------------------------ разбор
def reviews_count(seller_info):
    """«GR 5,0 16 отзывов Частное лицо…» → 16; «4,5 2 535 отзывов» → 2535; «Нет отзывов» → 0; нет блока → None.
    Рейтинг стоит вплотную к числу отзывов, поэтому сначала его вырезаем: иначе «4,8 22» читается как 822."""
    if not seller_info:
        return None
    s = re.sub(r"\d,\d", " ", seller_info.replace(" ", " "))
    if re.search(r"нет отзывов", s, re.I):
        return 0
    m = re.search(r"(\d{1,3}(?: \d{3})+|\d+)\s*отзыв", s)
    return int(m.group(1).replace(" ", "")) if m else 0


def _unescape(html):
    return html.replace('\\"', '"').replace("\\u002F", "/")


def _json_at(s, i):
    """JSON-значение, начинающееся в s[i] (массив или объект), по скобкам; None, если не разобралось."""
    open_ch = s[i]
    close_ch = {"[": "]", "{": "}"}.get(open_ch)
    if not close_ch:
        return None
    depth, instr, esc = 0, False, False
    for j in range(i, min(len(s), i + 400000)):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    return None
    return None


def photos(html):
    """Фото самого объявления: galleryInfo.media карточки. Ниже по странице те же картинки повторяются
    (imageUrls) и лежат фото «похожих», поэтому берём только галерею и без видео."""
    h = _unescape(html)
    i = h.find('"galleryInfo"')
    j = h.find('"media":[', i) if i >= 0 else -1
    media = _json_at(h, j + len('"media":')) if j >= 0 else None
    urls = []
    for m in media or []:
        if m.get("isVideo"):
            continue
        u = (m.get("urls") or {}).get("1280x960") or next(iter((m.get("urls") or {}).values()), None)
        if u and u not in urls:
            urls.append(u)
    return urls


def locality(html):
    """Город продавца: sellerAddressInfo.fullAddress.locality и метро/район (geoReferences)."""
    h = _unescape(html)
    i = h.find('"sellerAddressInfo"')
    if i < 0:
        return None, []
    seg = h[i:i + 6000]
    m = re.search(r'"fullAddress":\{"locality":"([^"]+)"', seg)
    geo = re.findall(r'"content":"([^"]+)"', seg[seg.find('"geoReferences"'):][:2000]) if '"geoReferences"' in seg else []
    return (m.group(1) if m else None), geo[:3]


def prefilter(w, it):
    """Отсев по выдаче — до карточки. Возвращает причину отказа или None."""
    t = it.get("title") or ""
    if w.get("title_must") and not re.search(w["title_must"], t, re.I):
        return "заголовок"
    if w.get("title_not") and re.search(w["title_not"], t, re.I):
        return "заголовок"
    p = it.get("price")
    if p and w.get("price_min") and p < w["price_min"]:
        return "цена"
    if p and w.get("price_max") and p > w["price_max"]:
        return "цена"
    return None


def page_url(url, n):
    return url if n == 1 else url + ("&" if "?" in url else "?") + f"p={n}"


# ------------------------------------------------------------------ проверка карточки
def code_checks(w, card, loc):
    """То, что проверяется без модели. Возвращает (отказ или None, факты для модели)."""
    facts = {}
    if card.get("closed"):
        return "снято с продажи", facts
    if card.get("unavailable"):
        return f"недоступно: {card['unavailable']}", facts
    n = reviews_count(card.get("sellerInfo"))
    facts["reviews"] = n
    if w.get("min_reviews") and n is None:
        return "нет блока продавца — отзывы не проверить", facts
    if w.get("min_reviews") and n < w["min_reviews"]:
        return "у продавца нет отзывов" if not n else f"отзывов {n} < {w['min_reviews']}", facts
    region = w.get("region")
    if region:
        allowed = [region] if isinstance(region, str) else list(region)
        city, geo = loc
        facts["city"], facts["geo"] = city, geo
        delivery = bool(re.search(r"доставк", card.get("safedeal") or "", re.I))
        if city and city not in allowed and not (w.get("delivery") and delivery):
            return f"город: {city}", facts
    p = card.get("price")
    if p and w.get("price_max") and p > w["price_max"]:
        return f"цена {p:.0f}", facts
    return None, facts


def _json_answer(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def judge(w, card, facts, files, workdir):
    """claude -p с одним Read: правила по описанию и фото. Возвращает (ответ модели, сколько фото открыто)."""
    with open(JUDGE, encoding="utf-8") as f:
        prompt = f.read()
    rules = list(w.get("rules") or [])
    if w.get("size"):
        rules.append(f"Размер {w['size']}")
    prompt += "\n\n## Правила покупателя\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    prompt += ("\n\nУже проверено кодом (не перепроверяй): продавец с отзывами"
               f" ({facts.get('reviews')}), город — {facts.get('city') or 'не указан'}"
               f"{' (' + ', '.join(facts.get('geo') or []) + ')' if facts.get('geo') else ''}.")
    view = {k: card.get(k) for k in ("title", "price", "date", "seller", "sellerInfo", "params", "description", "safedeal")}
    prompt += "\n\n## Карточка\n" + json.dumps(view, ensure_ascii=False, indent=1)
    prompt += "\n\n## Фото (открой каждое)\n" + ("\n".join(files) if files else "фото нет")
    env = dict(os.environ)
    cmd = [os.path.expanduser("~/.local/bin/claude") if os.path.exists(os.path.expanduser("~/.local/bin/claude")) else "claude",
           "-p", "--model", JUDGE_MODEL, "--tools", "Read", "--allowedTools", "Read",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=workdir, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return None, 0
    seen, result = set(), None
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "assistant":
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "tool_use" and c.get("name") == "Read":
                    fp = os.path.basename(str((c.get("input") or {}).get("file_path", "")))
                    if fp in files:
                        seen.add(fp)
        elif e.get("type") == "result":
            result = e.get("result")
    ans = _json_answer(result)
    if ans is None:
        print(f"  judge: нет JSON (rc={r.returncode}) {r.stderr[-300:]}", flush=True)
    return ans, len(seen)


def fetch_photos(urls, d):
    os.makedirs(d, exist_ok=True)
    files = []
    for i, u in enumerate(urls[:MAX_PHOTOS], 1):
        fp = os.path.join(d, f"{i}.jpg")
        subprocess.run(["curl", "-s", "-m", "25", "-o", fp, u], capture_output=True)
        if os.path.exists(fp) and os.path.getsize(fp) > 2000:
            subprocess.run(["sips", "-Z", "1280", fp], capture_output=True)
            files.append(f"{i}.jpg")
    return files


# ------------------------------------------------------------------ прогон
class Blocked(Exception):
    pass


def _launch_browser():
    """Окно Chrome for Testing на mini (браузер Playwright), иначе — Chrome, как на ноутбуке."""
    if not os.environ.get("AV_CHROME"):
        base = os.path.expanduser("~/Library/Caches/ms-playwright")
        for d in sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []:
            p = os.path.join(base, d, "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS",
                             "Google Chrome for Testing")
            if d.startswith("chromium-") and os.path.exists(p):
                os.environ["AV_CHROME"] = p
                break
    os.environ.setdefault("AV_PROFILE", os.path.join(WORK, "profile"))
    sys.path.insert(0, AV_DIR)
    import av  # noqa: E402 — playwright есть только в venv прогона
    av.PROFILE = os.environ["AV_PROFILE"]
    if os.environ.get("AV_CHROME"):
        av.CHROME = os.environ["AV_CHROME"]
    av.launch()
    return av


CHALLENGE_SETTLE = 30                       # «проверка безопасности» Qrator Chrome проходит сам за секунды
CAPTCHA_WAIT = int(os.environ.get("AVITO_CAPTCHA_WAIT_MIN", "45")) * 60   # сколько ждать человека


class Human:
    """Капча — дело человека: прогон шлёт уведомление и ждёт, пока пазл в окне пройдут, а не сдаётся.
    Окно при этом обязано жить: launchd убивает группу процессов агента, поэтому в plist AbandonProcessGroup."""

    def __init__(self, av, st, watches):
        self.av, self.st, self.watches = av, st, watches

    async def _title_blocked(self, page):
        try:
            return bool(re.search(self.av.BLOCK_RE, await page.title(), re.I))
        except Exception:
            return False

    async def clear(self, page):
        """True — вкладка чистая (сразу, после JS-проверки или после капчи руками); False — человек не пришёл."""
        import asyncio
        for _ in range(CHALLENGE_SETTLE // 3):
            if not await self._title_blocked(page):
                return True
            await asyncio.sleep(3)
        print(f"{iso()} капча: {await page.title()} — жду человека до {CAPTCHA_WAIT // 60} мин", flush=True)
        block_event(self.st, "captcha", self.watches)
        # Пока ждём — метка для сайта: главная показывает «Пройти проверку», avito-check.html — что прогон ждёт.
        self.st["captcha"] = {"since": iso(), "until": iso(now() + dt.timedelta(seconds=CAPTCHA_WAIT))}
        save_state(self.st)               # чтобы уведомление и метка ушли, пока мы ждём
        try:
            await page.bring_to_front()
        except Exception:
            pass
        deadline = time.monotonic() + CAPTCHA_WAIT
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(5)
                if not await self._title_blocked(page):
                    print(f"{iso()} капча пройдена, продолжаю", flush=True)
                    await asyncio.sleep(5)
                    return True
            return False
        finally:
            self.st.pop("captcha", None)
            save_state(self.st)

    async def fetch(self, page, url):
        """fetch_parse с одним повтором после капчи. Блок без капчи (пустой ответ, 429 без пазла) — как был."""
        r = await self.av.fetch_parse(page, url)
        if r.get("blocked") and await self._title_blocked(page):
            if not await self.clear(page):
                raise Blocked("captcha")
            r = await self.av.fetch_parse(page, url)
        return r


async def run_watches(watches, st, max_cards=None):
    import asyncio
    av = _launch_browser()
    p, b, page = await av.tab()
    human = Human(av, st, watches)
    try:
        if not await human.clear(page):
            raise Blocked("captcha")
        for w in watches:
            await run_one(human, page, w, st, max_cards)
            save_state(st)
            await asyncio.sleep(random.uniform(*PAGE_PAUSE))
    finally:
        await b.close()
        await p.stop()


async def run_one(human, page, w, st, max_cards=None):
    import asyncio
    ws = wstate(st, w["id"])
    seen = ws["seen"]
    first_run = not seen
    cov = {"pages": 0, "listings": 0, "new": 0, "passed": 0, "cards": 0, "photos": 0, "photos_total": 0,
           "queued": 0, "searches": [], "blocked": None}
    rep = {"at": iso(), "coverage": cov, "matches": [], "drops": [], "rejected": {}, "unsure": [], "done": False}
    ws["runs"] = (ws.get("runs") or [])[-29:] + [rep]
    print(f"{iso()} {w['id']}: {w.get('title')}", flush=True)
    try:
        await _search(human, page, w, seen, cov, rep, first_run)
        queue = _queue(w, seen, rep)
        ws["queue"] = queue
        cov["passed"] = len(queue)
        await _cards(human, page, w, seen, queue, cov, rep, max_cards)
        rep["done"] = not queue
    finally:
        ws["queue"] = [q for q in ws.get("queue", []) if not seen.get(q, {}).get("verdict")]
        cov["queued"] = len(ws["queue"])
        rep["finished"] = iso()
        if rep["matches"] or rep["drops"]:
            add_event(st, w, rep)      # найденное до блока тоже звонит
        print(f"  охват: {coverage_text(cov)}", flush=True)


async def _search(human, page, w, seen, cov, rep, first_run):
    """Выдача по дате, пока не пошли знакомые страницы. Новые id ложатся в seen без вердикта — это и есть
    очередь, поэтому блок посреди прогона ничего не теряет."""
    import asyncio
    for s in w.get("searches", []):
        sc = {"url": s["url"], "pages": 0, "listings": 0, "stop": ""}
        cov["searches"].append(sc)
        limit = int(s.get("pages", 3))
        for n in range(1, limit + 1):
            if cov["pages"]:
                await asyncio.sleep(random.uniform(*PAGE_PAUSE))
            r = await human.fetch(page, page_url(s["url"], n))
            if r.get("blocked") or r.get("shell"):
                cov["blocked"] = f"выдача, стр. {n}: {r.get('status') or r.get('title') or 'shell'}"
                raise Blocked(cov["blocked"])
            items = [it for it in (r.get("list") or []) if it.get("id")]
            sc["pages"] += 1
            sc["listings"] += len(items)
            cov["pages"] += 1
            cov["listings"] += len(items)
            unseen = 0
            for it in items:
                prev, price = seen.get(it["id"]), it.get("price")
                if prev is None:
                    unseen += 1
                    cov["new"] += 1
                    seen[it["id"]] = {"title": it.get("title"), "url": it.get("url"), "price": price,
                                      "first": iso(), "last": iso(), "prices": [[iso(), price]]}
                    continue
                prev["last"] = iso()
                if price and prev.get("price") and price != prev["price"]:
                    prev["prices"].append([iso(), price])
                    v = prev.get("verdict") or {}
                    if price < prev["price"] and v.get("verdict") == "match":
                        rep["drops"].append({"id": it["id"], "title": it.get("title"), "url": it.get("url"),
                                             "price": price, "was": prev["price"], "summary": v.get("summary")})
                if price:
                    prev["price"] = price
            if not items:
                sc["stop"] = f"стр. {n} пустая"
                break
            if not unseen and not first_run:
                sc["stop"] = f"стр. {n} целиком знакомая"
                break
            if len(items) < 30:
                sc["stop"] = f"стр. {n} последняя"
                break
        else:
            sc["stop"] = f"предел {limit} стр."


def _queue(w, seen, rep):
    """Всё увиденное без вердикта: отсев по заголовку и цене, прошедшие — в очередь, свежие первыми
    (выгодное уходит быстро)."""
    queue = []
    for lid, info in seen.items():
        if info.get("verdict"):
            continue
        why = prefilter(w, info)
        if why:
            info["verdict"] = {"verdict": "no", "why": why, "at": iso()}
            rep["rejected"][why] = rep["rejected"].get(why, 0) + 1
        else:
            queue.append(lid)
    queue.sort(key=lambda k: seen[k].get("first") or "", reverse=True)
    return queue


async def _cards(human, page, w, seen, queue, cov, rep, max_cards):
    import asyncio
    budget = max_cards if max_cards is not None else int(w.get("max_cards", 12))
    last_fetch, retry = None, []
    while queue and cov["cards"] < budget:
        lid = queue[0]
        info = seen[lid]
        if last_fetch is not None:     # минута-полторы между карточками, время модели входит в неё
            await asyncio.sleep(max(0, random.uniform(*CARD_PAUSE) - (time.monotonic() - last_fetch)))
        last_fetch = time.monotonic()
        r = await human.fetch(page, info["url"])
        if r.get("blocked"):
            cov["blocked"] = f"карточка {lid}: {r.get('status') or r.get('title')}"
            raise Blocked(cov["blocked"])
        queue.pop(0)
        cov["cards"] += 1
        card = r.get("card") or {}
        html = await page.content()
        if card.get("price"):
            info["price"] = card["price"]
        why, facts = code_checks(w, card, locality(html))
        if why:
            info["verdict"] = {"verdict": "no", "why": why, "at": iso(), **facts}
            key = why.split(":")[0][:40]
            rep["rejected"][key] = rep["rejected"].get(key, 0) + 1
            print(f"  {lid} нет: {why}", flush=True)
            continue
        d = os.path.join(WORK, "cards", lid)
        files = fetch_photos(photos(html), d)
        cov["photos_total"] += len(files)
        ans, nseen = judge(w, card, facts, files, d)
        cov["photos"] += nseen
        verdict = (ans or {}).get("verdict")
        if verdict not in ("match", "no", "unsure"):
            retry.append(lid)           # модель не ответила — карточку посмотрим в следующий прогон
            print(f"  {lid} модель не ответила", flush=True)
            continue
        rec = {"verdict": verdict, "at": iso(), "photos": nseen, "photos_total": len(files),
               "summary": ans.get("summary"), "flaws": ans.get("flaws"), "rules": ans.get("rules"),
               "seller": card.get("seller"), "reviews": facts.get("reviews"), "city": facts.get("city"),
               "geo": facts.get("geo"), "price": card.get("price") or info.get("price"),
               "title": card.get("title") or info.get("title")}
        info["verdict"] = rec
        print(f"  {lid} {verdict} ({nseen}/{len(files)} фото): {rec['summary']}", flush=True)
        if verdict == "match":
            rep["matches"].append({"id": lid, "url": info["url"], **rec})
        elif verdict == "unsure":
            rep["unsure"].append({"id": lid, "url": info["url"], "title": rec["title"], "price": rec["price"],
                                  "summary": rec["summary"]})
        else:
            rep["rejected"]["правила"] = rep["rejected"].get("правила", 0) + 1
    queue.extend(retry)


# ------------------------------------------------------------------ уведомления
def coverage_text(c):
    parts = [f"выдача {c['pages']} стр., {c['listings']} объявл., новых {c['new']}"]
    stops = [s["stop"] for s in c.get("searches", []) if s.get("stop")]
    if stops:
        parts[0] += f" (дальше не шёл: {'; '.join(stops)})"
    parts.append(f"по заголовку и цене прошли {c['passed']}, открыто карточек {c['cards']}")
    parts.append(f"фото просмотрено {c['photos']} из {c['photos_total']}")
    if c.get("queued"):
        parts.append(f"в очереди на следующий прогон {c['queued']}")
    return "; ".join(parts)


def _rub(x):
    return f"{int(x):,}".replace(",", " ") + " ₽" if x else "цена ?"


def add_event(st, w, rep):
    lines = []
    for m in rep["matches"]:
        who = f"{m.get('seller') or 'продавец'}, {m.get('reviews')} отз." if m.get("reviews") is not None else ""
        lines.append(f"{m.get('title')} — {_rub(m.get('price'))}" + (f", {who}" if who else "")
                     + (f". {m['summary']}" if m.get("summary") else "") + f" {m['url']}")
    for d in rep["drops"]:
        lines.append(f"Подешевело: {d.get('title')} — {_rub(d['price'])} (было {_rub(d['was'])}) {d['url']}")
    n = len(rep["matches"])
    head = f"{n} подходит" if n == 1 else f"{n} подходят" if n else "подешевело"
    st["events"].append({"id": f"avito:{w['id']}:{rep['at']}", "kind": "avito", "watch": w["id"],
                         "title": f"Авито: {w.get('title')} — {head}",
                         "body": "\n".join(lines) + "\n\nОхват: " + coverage_text(rep["coverage"]), "at": iso()})


def block_event(st, why, watches):
    """Одно уведомление в день о блоке/капче, а не тишина и не по звонку на каждый повтор."""
    day = now().date().isoformat()
    eid = f"avito-block:{day}"
    if any(e["id"] == eid for e in st["events"]):
        return
    names = ", ".join(w.get("title") or w["id"] for w in watches)
    if why == "captcha":
        body = (f"Авито просит пройти проверку. Открой BoW → «Пройти проверку Авито» на главной "
                f"(bodywithoutorgans.cc/avito-check.html): там окно mini, картинки отмечаются пальцем. Прогон ждёт {CAPTCHA_WAIT // 60} мин и сам продолжит; не успеешь — повтор в следующем слоте. "
                + names)
    else:
        body = f"Авито блокирует домашний IP ({why}). Остальное — в очереди, повтор позже сегодня. {names}"
    st["events"].append({"id": eid, "kind": "avito", "watch": None, "title": "Авито: прогон не прошёл",
                         "body": body, "at": iso()})


def notify_items(hours=24):
    """Для scripts/notify.py: свежие события слежки."""
    st = load_state()
    cut = now() - dt.timedelta(hours=hours)
    out = []
    for e in st.get("events", []):
        try:
            at = dt.datetime.fromisoformat(e["at"])
        except Exception:
            continue
        if at >= cut:
            out.append({"id": e["id"], "kind": "avito", "title": e["title"], "body": e["body"], "at": e["at"],
                        "page": "site"})
    return out


# ------------------------------------------------------------------ статус для сайта
def public():
    st = load_state()
    out = []
    for w in load_watches():
        ws = st["watches"].get(w["id"]) or {}
        runs = ws.get("runs") or []
        last = runs[-1] if runs else None
        matches = [dict(id=k, url=v.get("url"), **{x: v["verdict"].get(x) for x in
                                                    ("title", "price", "summary", "flaws", "seller", "reviews", "at")})
                   for k, v in (ws.get("seen") or {}).items() if (v.get("verdict") or {}).get("verdict") == "match"]
        for m in matches:
            m["price"] = ws["seen"][m["id"]].get("price") or m.get("price")
            m["lastSeen"] = ws["seen"][m["id"]].get("last")
        matches.sort(key=lambda m: m.get("at") or "", reverse=True)
        unsure = sum(1 for v in (ws.get("seen") or {}).values() if (v.get("verdict") or {}).get("verdict") == "unsure")
        out.append({"id": w["id"], "title": w.get("title"), "active": w.get("active", True),
                    "rules": w.get("rules"), "size": w.get("size"), "region": w.get("region"),
                    "seen": len(ws.get("seen") or {}), "queue": len(ws.get("queue") or []), "unsure": unsure,
                    "matches": matches[:MATCHES_SHOWN],
                    "last": last and {"at": last["at"], "done": last.get("done"), "blocked": last["coverage"].get("blocked"),
                                      "coverage": coverage_text(last["coverage"]), "cards": last["coverage"]["cards"],
                                      "photos": last["coverage"]["photos"]}})
    return {"watches": out, "lastRun": st.get("lastRun"), "captcha": st.get("captcha")}


# ------------------------------------------------------------------ команды
def due(st=None):
    """Есть ли сегодня что делать: прогона ещё не было, или он упёрся в блок / оставил очередь."""
    st = st or load_state()
    lr = st.get("lastRun") or {}
    today = now().date().isoformat()
    if not lr.get("at", "").startswith(today):
        return True, "сегодня прогона ещё не было"
    if lr.get("outcome") != "ok":
        return True, f"сегодня был {lr.get('outcome')}"
    queued = sum(len((st["watches"].get(w["id"]) or {}).get("queue") or []) for w in load_watches() if w.get("active", True))
    return (True, f"в очереди {queued}") if queued else (False, "сегодня всё сделано")


def cmd_run(a):
    st = load_state()
    if not a.force:
        ok, why = due(st)
        if not ok:
            print(f"{iso()} пропускаю: {why}")
            return
        print(f"{iso()} {why}")
    watches = [w for w in load_watches() if w.get("active", True) and (not a.watch or w["id"] == a.watch)]
    if not watches:
        print("нет активных слежек")
        return
    import asyncio
    outcome = "ok"
    try:
        asyncio.run(run_watches(watches, st, a.max_cards))
    except Blocked as e:
        outcome = "captcha" if str(e) == "captcha" else "blocked"
        print(f"{iso()} блок: {e}", flush=True)
        block_event(st, str(e), watches)
    except Exception as e:  # noqa: BLE001 — упавший прогон тоже должен остаться в отчёте
        outcome = f"error: {type(e).__name__}: {e}"[:200]
        print(f"{iso()} сбой: {outcome}", flush=True)
        if not (st.get("lastRun") or {}).get("outcome", "ok").startswith("error"):
            block_event(st, outcome, watches)
    st["lastRun"] = {"at": iso(), "outcome": outcome}
    save_state(st)


# На mini нет git (deploy.sh не возит .git), на ноутбуке это репозиторий. Путь сравнивать нельзя: ~/movies на
# mini — это ~/Movies, файловая система без учёта регистра.
ON_MINI = not os.path.isdir(os.path.join(ROOT, ".git")) or bool(os.environ.get("AVITO_LOCAL"))


def proxy(argv):
    """С ноутбука — та же команда на mini: слежки и состояние живут там."""
    import shlex
    if argv and argv[0] == "run":
        remote = "cd movies && sh scripts/avito_watch.sh " + " ".join(shlex.quote(x) for x in argv[1:])
    else:
        remote = "cd movies && ~/.local/python312/bin/python3 scripts/avito_watch.py " + " ".join(shlex.quote(x) for x in argv)
    sys.exit(subprocess.call(["ssh", MINI, remote]))


def cmd_add(a):
    w = {"id": a.id, "title": a.title, "active": True, "created": now().date().isoformat(),
         "searches": [{"url": u if "s=" in u else u + ("&" if "?" in u else "?") + "s=104", "pages": a.pages}
                      for u in a.url],
         "title_must": a.title_must, "title_not": a.title_not, "price_min": a.price_min, "price_max": a.price_max,
         "size": a.size, "region": a.region, "delivery": a.delivery, "min_reviews": a.min_reviews,
         "rules": a.rule, "max_cards": a.max_cards}
    w = {k: v for k, v in w.items() if v not in (None, [], "")}
    if os.path.exists(watch_path(a.id)) and not a.replace:
        sys.exit(f"{a.id} уже есть — --replace, чтобы переписать")
    save_watch(w)
    print(json.dumps(w, ensure_ascii=False, indent=2))


def cmd_toggle(a, active):
    with open(watch_path(a.id), encoding="utf-8") as f:
        w = json.load(f)
    w["active"] = active
    w["stopped" if not active else "restarted"] = now().date().isoformat()
    save_watch(w)
    print(f"{a.id}: {'слежу' if active else 'остановлено'}")


def cmd_list():
    pub = public()
    for w in pub["watches"]:
        last = w.get("last") or {}
        print(f"{'●' if w['active'] else '○'} {w['id']}: {w['title']} — видел {w['seen']}, подходит {len(w['matches'])},"
              f" под вопросом {w['unsure']}, очередь {w['queue']}")
        if last:
            print(f"    {last['at']}: {last['coverage']}" + (f" — БЛОК {last['blocked']}" if last.get("blocked") else ""))
    if pub.get("lastRun"):
        print("последний прогон:", pub["lastRun"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("due")
    s = sub.add_parser("show"); s.add_argument("id")
    r = sub.add_parser("run"); r.add_argument("--watch"); r.add_argument("--force", action="store_true")
    r.add_argument("--max-cards", type=int)
    ad = sub.add_parser("add")
    ad.add_argument("id"); ad.add_argument("--title", required=True); ad.add_argument("--url", action="append", required=True)
    ad.add_argument("--rule", action="append", default=[]); ad.add_argument("--price-min", type=int)
    ad.add_argument("--price-max", type=int); ad.add_argument("--title-must"); ad.add_argument("--title-not")
    ad.add_argument("--size"); ad.add_argument("--region"); ad.add_argument("--delivery", action="store_true")
    ad.add_argument("--min-reviews", type=int, default=1); ad.add_argument("--pages", type=int, default=3)
    ad.add_argument("--max-cards", type=int, default=12); ad.add_argument("--replace", action="store_true")
    for name in ("stop", "start"):
        t = sub.add_parser(name); t.add_argument("id")
    a = ap.parse_args()
    if not ON_MINI:
        proxy(sys.argv[1:])
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "add":
        cmd_add(a)
    elif a.cmd in ("stop", "start"):
        cmd_toggle(a, a.cmd == "start")
    elif a.cmd == "list":
        cmd_list()
    elif a.cmd == "status":
        print(json.dumps(public(), ensure_ascii=False, indent=1))
    elif a.cmd == "due":
        ok, why = due()
        print(why)
        sys.exit(0 if ok else 1)
    elif a.cmd == "show":
        with open(watch_path(a.id), encoding="utf-8") as f:
            print(f.read())
        w = next((x for x in public()["watches"] if x["id"] == a.id), None)
        print(json.dumps(w, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
