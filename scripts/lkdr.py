#!/usr/bin/env python3
"""Чеки из «Мои чеки онлайн» (lkdr.nalog.ru) — все, а не только те, что ОФД прислал на почту.

Почтовый конвейер (`scripts/pantrylib/`) видит ровно то, что магазин согласился прислать письмом:
1151 чек, из них с позициями 597, и почти все — ВкусВилл. ФНС же складывает в личный кабинет каждый чек,
где на кассе назвали телефон, — с наименованиями товаров, ценами и продавцом. Это тот же источник, но полный.

API закрытый, поэтому ходим ровно так же, как ходит сайт (сверено с двумя открытыми клиентами,
см. CLAUDE.md): вход по SMS, дальше пара «токен на час + refreshToken», и два метода — список чеков и
фискальные данные одного чека.

Вход стоит за Яндекс-капчей (`/v2/auth/challenge/phone/start` отвечает `empty.captcha`), поэтому первый
шаг делает человек в браузере, а скрипт перенимает ключ и дальше живёт сам: refreshToken обновляет часовой
токен без всякой капчи. В консоли браузера на lkdr.nalog.ru, уже залогинившись с «запомнить меня»:

    copy(JSON.stringify({deviceId: localStorage.getItem('sourceDeviceId'),
      refreshToken: localStorage.getItem('refresh.token') || sessionStorage.getItem('refresh.token')}))

  pbpaste | python3 scripts/lkdr.py adopt      # перенести вход (ключ не проходит через терминал)
  python3 scripts/lkdr.py sync                 # забрать новые чеки (и позиции к ним)
  python3 scripts/lkdr.py sync --full          # перечитать всё с нуля
  python3 scripts/lkdr.py stats                # что накопилось

`login`/`code` оставлены на случай, если капчу когда-нибудь снимут или её токен будет откуда взять
(`login --captcha <токен>`).
"""
import argparse
import datetime as dt
import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
AUTH_FILE = os.path.join(DATA, "lkdr-auth.json")        # refreshToken — секрет, файл в .gitignore и 0600
STORE = os.path.join(DATA, "lkdr-receipts.json")

BASE = "https://lkdr.nalog.ru/api"
# Пути сверены с бандлом самого сайта (static/js/main.*.chunk.js): вход живёт в v2 и называется `phone`,
# а подтверждение кода и обновление токена так и остались в v1. Старые v1/challenge/sms/* отвечают 404.
START = "/v2/auth/challenge/phone/start"
VERIFY = "/v1/auth/challenge/phone/verify"
TOKEN = "/v1/auth/token"
RECEIPTS = "/v1/receipt"
FISCAL = "/v1/receipt/fiscal_data"
PROFILE = "/v1/user/profile"
CODE_SENT = "registration.sms.verification.not.expired"   # «код ещё не протух» — не ошибка
PAGE = 100            # сайт просит по 10; сотня проходит и экономит запросы (при 422 откатываемся на 10)
PAGE_FALLBACK = 10
DELAY = 0.1           # пауза между запросами, чтобы не ловить 429
TIMEOUT = 30
ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


class LkdrError(Exception):
    def __init__(self, msg, status=None, code=None):
        super().__init__(msg)
        self.status, self.code = status, code


# ---------------------------------------------------------------- состояние
def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    if private:
        os.chmod(path, 0o600)


def auth_state():
    st = load(AUTH_FILE, {})
    if not st.get("deviceId"):
        # Постоянный идентификатор устройства: ФНС привязывает к нему refreshToken, так что он должен
        # пережить перезапуск. Формат — как у сайта, 21 символ nanoid.
        st["deviceId"] = "".join(secrets.choice(string.ascii_letters + string.digits + "-_") for _ in range(21))
        save(AUTH_FILE, st, private=True)
    return st


def device_info(st):
    return {"sourceDeviceId": st["deviceId"], "sourceType": "WEB", "appVersion": "1.0.0",
            "metaDetails": {"userAgent": UA}}


# ---------------------------------------------------------------- запросы
def api(path, payload, token=None, method="POST"):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json;charset=UTF-8", "Accept": "application/json",
               "Origin": "https://lkdr.nalog.ru", "Referer": "https://lkdr.nalog.ru/", "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    last = None
    for attempt in range(ATTEMPTS):
        req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                info = json.loads(raw)
            except ValueError:
                info = {}
            msg = info.get("message") or raw[:200] or e.reason
            last = LkdrError(f"{path}: {e.code} {msg}", status=e.code, code=info.get("code"))
            if e.code not in RETRY_STATUS:
                raise last
        except urllib.error.URLError as e:
            last = LkdrError(f"{path}: сеть недоступна ({e.reason})")
        time.sleep(1.0 * (attempt + 1))
    raise last


def token(st=None):
    """Действующий токен: живёт час, поэтому обновляется по refreshToken, а не спрашивается заново."""
    st = st or auth_state()
    if not st.get("refreshToken"):
        raise LkdrError("нет refreshToken — сначала `lkdr.py login <телефон>` и `lkdr.py code <из SMS>`")
    left = 0
    if st.get("tokenExpires"):
        try:
            # tokenExpireIn приходит в UTC («…Z»). Сравнивать его с местным временем нельзя: в Москве
            # токен считался бы протухшим на три часа раньше срока, и обновлялся бы на каждый запрос.
            exp = dt.datetime.fromisoformat(st["tokenExpires"]).replace(tzinfo=dt.timezone.utc)
            left = (exp - dt.datetime.now(dt.timezone.utc)).total_seconds()
        except ValueError:
            left = 0
    if st.get("token") and left > 300:
        return st["token"]
    out = api(TOKEN, {"refreshToken": st["refreshToken"], "deviceInfo": device_info(st)})
    st["token"] = out["token"]
    st["tokenExpires"] = (out.get("tokenExpireIn") or "").replace("Z", "")
    if out.get("refreshToken"):
        st["refreshToken"] = out["refreshToken"]
    save(AUTH_FILE, st, private=True)
    return st["token"]


# ---------------------------------------------------------------- вход
def login(phone, captcha=None):
    st = auth_state()
    phone = "".join(c for c in phone if c.isdigit())
    body = {"phone": phone, "deviceInfo": device_info(st)}
    if captcha:
        body["captchaToken"] = captcha
    try:
        out = api(START, body)
    except LkdrError as e:
        # «Код уже отправлен» — это не ошибка: SMS в пути, а прежний challengeToken ещё действует.
        if e.code == CODE_SENT and st.get("challengeToken") and st.get("phone") == phone:
            return f"SMS уже была отправлена на {phone}. Введи код: lkdr.py code <6 цифр>"
        if e.code == "empty.captcha":
            raise LkdrError("вход по SMS закрыт Яндекс-капчей — её проходит человек в браузере. "
                            "Войди на lkdr.nalog.ru и перенеси ключ: `lkdr.py adopt` (см. докстринг)") from None
        raise
    st.update(phone=phone, challengeToken=out["challengeToken"])
    save(AUTH_FILE, st, private=True)
    return f"SMS отправлена на {phone}, код живёт {out.get('challengeTokenExpiresInSec', '?')} с. Дальше: lkdr.py code <6 цифр>"


def code(sms):
    st = auth_state()
    if not st.get("challengeToken"):
        raise LkdrError("сначала `lkdr.py login <телефон>`")
    out = api(VERIFY, {"challengeToken": st["challengeToken"], "phone": st["phone"],
                       "code": "".join(c for c in sms if c.isdigit()),
                       "deviceInfo": device_info(st)})
    st.update(refreshToken=out["refreshToken"], token=out.get("token"),
              tokenExpires=(out.get("tokenExpireIn") or "").replace("Z", ""))
    st.pop("challengeToken", None)
    save(AUTH_FILE, st, private=True)
    who = ((out.get("profile") or {}).get("taxpayerPerson") or {}).get("phone") or st["phone"]
    return f"вход выполнен ({who}); refreshToken сохранён в {os.path.relpath(AUTH_FILE, ROOT)}"


def adopt(raw=None):
    """Перенести вход из браузера. Капчу проходит человек — один раз, руками; дальше пары
    `deviceId + refreshToken` хватает навсегда: часовой токен обновляется сам и капчи не требует
    (сайт делает ровно это в своём перехватчике 401).

    В консоли браузера на lkdr.nalog.ru:
        copy(JSON.stringify({deviceId: localStorage.getItem('sourceDeviceId'),
          refreshToken: localStorage.getItem('refresh.token') || sessionStorage.getItem('refresh.token')}))
    и затем:  pbpaste | python3 scripts/lkdr.py adopt
    """
    raw = raw or sys.stdin.read()
    try:
        got = json.loads(raw.strip())
    except ValueError:
        raise LkdrError("на вход нужен JSON вида {\"deviceId\": \"…\", \"refreshToken\": \"…\"}") from None
    dev, ref = (got.get("deviceId") or "").strip(), (got.get("refreshToken") or "").strip()
    if not dev or not ref:
        raise LkdrError("в JSON нет deviceId или refreshToken — в браузере нужно войти с «запомнить меня», "
                        "иначе ключи лежат в sessionStorage и localStorage.getItem вернёт null")
    st = auth_state()
    st.update(deviceId=dev, refreshToken=ref)
    st.pop("token", None)
    st.pop("tokenExpires", None)
    save(AUTH_FILE, st, private=True)
    tok = token(st)                     # сразу проверяем, что ключ живой
    who = api(PROFILE, None, tok, method="GET")
    person = ((who.get("user") or {}).get("taxpayerPerson") or {})
    st["phone"] = person.get("phone") or st.get("phone")
    save(AUTH_FILE, st, private=True)
    return f"вход перенесён: {person.get('phone') or 'профиль получен'}; дальше `lkdr.py sync`"


# ---------------------------------------------------------------- чеки
def receipt_page(tok, offset, limit, date_from=None, date_to=None):
    return api(RECEIPTS, {"limit": limit, "offset": offset, "dateFrom": date_from, "dateTo": date_to,
                            "orderBy": "CREATED_DATE:DESC", "inn": None, "kktOwner": ""}, tok)


def fiscal(tok, key):
    return api(FISCAL, {"key": key}, tok)


MAX_TRIES = 2   # у чеков старше пяти лет позиций уже нет: ФНС отдаёт «сервис временно недоступен» всегда


def sync(full=False, date_from=None, limit=None, delay=DELAY, retry_errors=False, verbose=True):
    """Новые чеки сверху вниз. Список идёт от свежих к старым, поэтому обычная синхронизация
    останавливается на первом уже известном чеке; `--full` дочитывает до конца (и добирает позиции
    к тем чекам, у которых их ещё нет)."""
    st = auth_state()
    tok = token(st)
    store = load(STORE, {})
    limit = limit or PAGE
    offset, added, seen_known, stop = 0, 0, 0, False
    while not stop:
        try:
            page = receipt_page(tok, offset, limit, date_from)
        except LkdrError as e:
            if e.status == 422 and limit != PAGE_FALLBACK:
                limit = PAGE_FALLBACK        # сотня не понравилась — просим как сайт
                continue
            raise
        rows = page.get("receipts") or []
        for r in rows:
            key = r.get("key")
            if not key:
                continue
            if key in store:
                seen_known += 1
                if not full:
                    stop = True
                    break
                continue
            store[key] = {"receipt": r, "fiscal": None, "fetched": None}
            added += 1
        if not stop:
            if len(rows) < limit or page.get("hasMore") is False:
                break
            offset += len(rows)
            time.sleep(delay)
        if verbose:
            print(f"  список: {len(store)} чеков, новых {added}", end="\r", flush=True)
    if verbose:
        print()
    # Позиции: отдельный запрос на чек, поэтому берём только те, где их ещё нет. У чеков 2018–2020 годов
    # их и не будет никогда — ФНС держит фискальные данные около пяти лет и отвечает «сервис временно
    # недоступен»; после MAX_TRIES такой чек больше не трогаем, иначе каждая синхронизация тратила бы
    # сотню запросов впустую.
    need = [k for k, v in store.items()
            if not v.get("fiscal") and (retry_errors or v.get("tries", 0) < MAX_TRIES)]
    for i, key in enumerate(need, 1):
        store[key]["tries"] = store[key].get("tries", 0) + 1
        try:
            store[key]["fiscal"] = fiscal(tok, key)
            store[key]["fetched"] = dt.datetime.now().isoformat(timespec="seconds")
            store[key].pop("error", None)
        except LkdrError as e:
            store[key]["error"] = str(e)
        if verbose and (i % 10 == 0 or i == len(need)):
            print(f"  позиции: {i}/{len(need)}", end="\r", flush=True)
        if i % 50 == 0:
            save(STORE, store)              # долгая выкачка не должна пропасть от одной ошибки
        time.sleep(delay)
    if verbose and need:
        print()
    save(STORE, store)
    return {"total": len(store), "added": added, "details": len(need),
            "errors": sum(1 for v in store.values() if v.get("error")),
            "given_up": sum(1 for v in store.values() if v.get("error") and v.get("tries", 0) >= MAX_TRIES)}


# ---------------------------------------------------------------- отчёт
def items(entry):
    return ((entry.get("fiscal") or {}).get("items")) or []


def when(entry):
    f, r = entry.get("fiscal") or {}, entry.get("receipt") or {}
    return (f.get("dateTime") or r.get("createdDate") or "")[:10]


def total(entry):
    f, r = entry.get("fiscal") or {}, entry.get("receipt") or {}
    try:
        return float(f.get("totalSum") if f.get("totalSum") is not None else r.get("totalSum") or 0)
    except (TypeError, ValueError):
        return 0.0


def seller(entry):
    f, r = entry.get("fiscal") or {}, entry.get("receipt") or {}
    return (f.get("user") or r.get("kktOwner") or "—").strip().strip('"')


def stats():
    import collections
    store = load(STORE, {})
    if not store:
        return "Пусто: сначала `lkdr.py sync`."
    dates = sorted(d for d in (when(v) for v in store.values()) if d)
    months, merchants = collections.Counter(), collections.Counter()
    sums = collections.Counter()
    n_items = 0
    for v in store.values():
        d = when(v)
        if d:
            months[d[:7]] += 1
            sums[d[:7]] += total(v)
        merchants[seller(v)] += 1
        n_items += len(items(v))
    lines = [f"Чеков: {len(store)}, с позициями: {sum(1 for v in store.values() if items(v))}, позиций всего: {n_items}",
             f"Период: {dates[0]} — {dates[-1]}" if dates else "Дат нет",
             "", "Последние месяцы:"]
    for m in sorted(months)[-12:]:
        lines.append(f"  {m}  {round(sums[m]):>8} ₽  {months[m]:>3} чеков")
    lines.append("")
    lines.append("Продавцы (топ-15):")
    for name, n in merchants.most_common(15):
        lines.append(f"  {n:>4}  {name[:60]}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login"); p.add_argument("phone"); p.add_argument("--captcha", help="токен Яндекс-капчи, если сервис его требует")
    p = sub.add_parser("code"); p.add_argument("sms")
    p = sub.add_parser("sync")
    p.add_argument("--full", action="store_true")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--limit", type=int)
    p.add_argument("--delay", type=float, default=DELAY, help="пауза между запросами, с")
    p.add_argument("--retry-errors", action="store_true", help="снова попробовать чеки, на которых сдались")
    p = sub.add_parser("adopt"); p.add_argument("json", nargs="?", help="по умолчанию читается со stdin")
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "login":
            print(login(a.phone, a.captcha))
        elif a.cmd == "code":
            print(code(a.sms))
        elif a.cmd == "adopt":
            print(adopt(a.json))
        elif a.cmd == "sync":
            out = sync(full=a.full, date_from=a.date_from, limit=a.limit, delay=a.delay,
                       retry_errors=a.retry_errors)
            print(f"чеков в базе {out['total']}, новых {out['added']}, дозагружено позиций {out['details']}"
                  + (f", без позиций {out['errors']}" if out["errors"] else "")
                  + (f" (из них {out['given_up']} старше пяти лет — больше не спрашиваем)" if out["given_up"] else ""))
        elif a.cmd == "stats":
            print(stats())
    except LkdrError as e:
        print(f"lkdr: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
