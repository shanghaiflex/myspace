#!/usr/bin/env python3
"""Самопроверка BoW: молчит, пока всё работает, и говорит одним уведомлением, когда что-то сломалось.

Итоги недели молчали четыре недели (20.09–18.10.2026): launchd не мог открыть лог агента и падал с
`exit 78` до старта скрипта, а в лог не писалось ни строки — снаружи это не было видно никак. Поэтому
проверяется не «процесс жив», а результат, и то, что видно только самому launchd:

  агенты   — каждый cc.bodywithoutorgans.* (~/Library/LaunchAgents и /Library/LaunchDaemons): загружен ли,
             код последнего выхода, KeepAlive держится ли, лог создан ли самим launchd (его подпись в com.apple.macl, иначе
             следующий запуск — exit 78, см. deploy/claim-log.sh) и записан ли лог после последнего запуска
             по расписанию;
  данные   — последняя пачка с телефона старше INGEST_STALE_H днём (INGEST_DAY), когда телефон синкается;
  профиль  — профиль приложения уже истёк (за 36 ч до конца предупреждает notify.profile_item);
  сайт     — https://bodywithoutorgans.cc/healthz снаружи (VPN → Cloudflare → тоннель → serve).

Шумного не звонит: падение часового агента считается сбоем, только если держится дольше полутора периодов
(`confirm`), сайт — две проверки подряд (сторож VPN чинит его за минуты). Состояние — data/selfcheck.json
(данные машины): там у каждой проблемы `since`, и уведомление `sys:<since>` (notify.system_item) меняет id
только когда появилась новая проблема, — пока сломано то же самое, телефон не звонит второй раз.

launchd cc.bodywithoutorgans.selfcheck (deploy/install-selfcheck.sh), раз в CHECK_EVERY. Лог пишет только
перемены (+ появилось / − прошло).

Usage:
  selfcheck.py run          проверить и сохранить (это делает launchd)
  selfcheck.py show         проверить и напечатать, ничего не сохраняя
  selfcheck.py status       то, что видят главная и телефон (data/selfcheck.json)
"""
import datetime as dt
import glob
import json
import os
import plistlib
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health as H  # noqa: E402

STATE = os.path.join(ROOT, "data", "selfcheck.json")
PREFIX = "cc.bodywithoutorgans."
CHECK_EVERY = 900           # период агента, с
STALE_AFTER = 3600          # data/selfcheck.json старше — сама самопроверка не работает
GRACE_MIN = 45              # агент по расписанию должен записать лог до запуска + GRACE_MIN
SITE_URL = "https://bodywithoutorgans.cc/healthz"
SITE_CONFIRM = 600          # сайт лежит две проверки подряд
KEEPALIVE_CONFIRM = 600     # serve/тоннель не поднялся за 10 минут
INGEST_STALE_H = 8
INGEST_DAY = (10, 23)       # местные часы, когда телефон обязан что-то прислать
SKIP = {"claimlog"}         # одноразовый агент из deploy/claim-log.sh
QUIET_LOGS = {"vpnwatchdog", "lampschedule", "selfcheck", "vpn"}   # пишут в лог только события
NAMES = {
    "serve": "Сайт (serve)", "tunnel": "Тоннель", "vpn": "VPN", "vpnwatchdog": "Сторож VPN",
    "directroutes": "Прямые маршруты", "health": "Заметка о здоровье", "healthbackup": "Бэкап health.db",
    "week": "Итоги недели", "mixrecs": "Советы: миксы", "tasterecs": "Советы: фильмы, книги, лекции",
    "reads": "Советы: статьи", "french": "Французский", "races": "Старты", "pantry": "Еда",
    "lkdr": "Чеки ФНС", "sensors": "Датчики", "lampschedule": "Расписание света",
    "vacuum": "Расписание уборки", "selfcheck": "Самопроверка",
}


def _short(label):
    return label[len(PREFIX):]


def _inside(path):
    """Внутри сайта? ~/movies и ~/Movies — одна папка (диск регистронезависимый), ROOT бывает любым из двух."""
    return path.lower().startswith(ROOT.lower() + "/")


def _rel(path):
    return path[len(ROOT) + 1:] if _inside(path) else path


def agents():
    """[(domain, label, plist, mtime)] — все наши агенты и демоны, кроме одноразовых."""
    out = []
    for pattern, domain in ((os.path.expanduser("~/Library/LaunchAgents/") + PREFIX + "*.plist", f"gui/{os.getuid()}"),
                            ("/Library/LaunchDaemons/" + PREFIX + "*.plist", "system")):
        for f in sorted(glob.glob(pattern)):
            try:
                with open(f, "rb") as fh:
                    pl = plistlib.load(fh)
            except Exception:
                continue
            label = pl.get("Label") or os.path.basename(f)[:-6]
            if _short(label) not in SKIP:
                out.append((domain, label, pl, os.path.getmtime(f)))
    return out


def launchd(domain, label):
    """Что знает launchd: {loaded, running, exit, runs}. `exit` — None, если ещё не выходил."""
    r = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"loaded": False}
    t = r.stdout
    code = re.search(r"^\s*last exit code = (-?\d+)", t, re.M)
    return {"loaded": True, "running": bool(re.search(r"^\s*state = running", t, re.M)),
            "exit": int(code.group(1)) if code else None}


def _matches(spec, t):
    wd = (t.weekday() + 1) % 7          # у launchd воскресенье — 0 (и 7)
    return (spec.get("Minute", t.minute) == t.minute and spec.get("Hour", t.hour) == t.hour
            and spec.get("Day", t.day) == t.day and spec.get("Month", t.month) == t.month
            and spec.get("Weekday", wd) % 7 == wd)


def last_fire(cal, now):
    """Последний момент по StartCalendarInterval не позже now (местное время), в пределах 32 дней."""
    specs = cal if isinstance(cal, list) else [cal]
    t = now.replace(second=0, microsecond=0)
    for _ in range(32 * 24 * 60):
        if any(_matches(s, t) for s in specs):
            return t
        t -= dt.timedelta(minutes=1)
    return None


def macl(path):
    """UUID-ы из xattr com.apple.macl: кто создал файл. Записи по 18 байт — 2 байта заголовка и UUID."""
    r = subprocess.run(["xattr", "-px", "com.apple.macl", path], capture_output=True, text=True)
    raw = "".join(r.stdout.split()) if r.returncode == 0 else ""
    return {raw[i + 4:i + 36] for i in range(0, len(raw), 36) if raw[i:i + 36].strip("0")}


def launchd_ids(found):
    """Подпись launchd по доменам — из логов агентов, которые запустились и вышли с нулём: launchd их открыл.
    Не зашита в код: UUID свой на каждой машине. Так claim-log.sh и узнаёт её, пробным файлом."""
    seen = {}
    for domain, pl, st in found:
        log = pl.get("StandardOutPath")
        if st.get("loaded") and st.get("exit") == 0 and log and _inside(log) and os.path.exists(log):
            seen.setdefault(domain, []).append(macl(log))
    # Большинством, а не объединением: один лог, пересозданный по ssh после удачного запуска, иначе внёс бы
    # в подпись sshd, и все созданные по ssh логи стали бы выглядеть своими.
    return {d: {u for u in set().union(*sets) if sum(u in x for x in sets) * 2 > len(sets)} for d, sets in seen.items()}


def _owned(log, ids):
    """Лог отдан launchd? Нужно только внутри ~/movies (это ~/Movies под TCC, см. deploy/claim-log.sh).
    Сам по себе macl ничего не значит: файл, созданный по ssh, тоже его получает — с подписью sshd."""
    if not ids or not _inside(log) or not os.path.exists(log):
        return True
    return bool(macl(log) & ids)


def check_agent(domain, label, pl, plist_mtime, now, st, ids):
    """Одна проблема на агента или None: (key, title, detail, confirm_seconds)."""
    short = _short(label)
    key, title = "job:" + short, NAMES.get(short, short)
    log = pl.get("StandardOutPath") or pl.get("StandardErrorPath")
    claim = f"sh deploy/claim-log.sh {_rel(log)}" if log else ""
    if not st["loaded"]:
        return key, title, f"агент {label} не загружен в launchd — переустанови его deploy/install-*.sh", 0
    if log and not _owned(log, ids.get(domain)):
        what = "launchd не может открыть свой лог (exit 78)" if st.get("exit") == 78 else \
            "лог создан не launchd, следующий запуск упадёт с exit 78"
        return key, title, f"{what}: {claim}", 0
    if pl.get("KeepAlive"):
        if not st["running"]:
            return key, title, f"не работает (последний код {st.get('exit')}) — смотри {_rel(log) if log else label}", KEEPALIVE_CONFIRM
        return None
    code = st.get("exit")
    interval = pl.get("StartInterval")
    if code not in (None, 0) and not st["running"]:
        where = f" — смотри {_rel(log)}" if log else ""
        confirm = max(int(interval * 1.5), CHECK_EVERY - 60) if interval else 0
        return key, title, f"последний запуск кончился кодом {code}{where}", confirm
    if not log or short in QUIET_LOGS or st["running"]:
        return None
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(log), H.tz()) if os.path.exists(log) else None
    if interval:
        due = now - dt.timedelta(seconds=2 * interval + 900)
        if dt.datetime.fromtimestamp(plist_mtime, H.tz()) < due and (not mtime or mtime < due):
            return key, title, f"лог не пишется с {_when(mtime, now)}, а должен раз в {_period(interval)}", 0
    elif pl.get("StartCalendarInterval"):
        fire = last_fire(pl["StartCalendarInterval"], now.replace(tzinfo=None))
        if fire:
            fire = fire.replace(tzinfo=H.tz())
            installed = dt.datetime.fromtimestamp(plist_mtime, H.tz())
            if now > fire + dt.timedelta(minutes=GRACE_MIN) and installed < fire and \
                    (not mtime or mtime < fire - dt.timedelta(minutes=1)):
                return key, title, f"не отработал по расписанию {_when(fire, now)}: лог последний раз {_when(mtime, now)}", 0
    return None


def _period(sec):
    return f"{sec // 3600} ч" if sec >= 3600 else f"{sec // 60} мин"


def _when(t, now):
    if not t:
        return "никогда"
    t = t.astimezone(H.tz())
    d = (now.date() - t.date()).days
    day = "сегодня" if d == 0 else "вчера" if d == 1 else t.strftime("%d.%m")
    return f"{day} в {t:%H:%M}"


def check_phone(now):
    """Истёкший профиль приложения или тишина от телефона днём. Профиль — причина, тишина — следствие."""
    out = []
    try:
        db = H.connect()
        ap = H.app_profile(db)
        li = H.last_ingest(db)
    except Exception as e:
        return [("ingest", "Данные с телефона", f"health.db не читается: {type(e).__name__}: {e}", 0)]
    exp = H.parse_ts(ap["expires"]) if ap and ap.get("expires") else None
    if exp and exp <= now:
        out.append(("profile", "Профиль BoW", f"истёк {_when(exp, now)}: приложение не запускается и ничего не шлёт — "
                    "подключи телефон и пересобери (sh ~/workspace/bow/phone.sh)", 0))
        return out
    if li and INGEST_DAY[0] <= now.hour < INGEST_DAY[1]:
        last = H.parse_ts(li)
        if now - last > dt.timedelta(hours=INGEST_STALE_H):
            hours = int((now - last).total_seconds() // 3600)
            out.append(("ingest", "Данные с телефона", f"последняя пачка {_when(last, now)} ({hours} ч назад): "
                        "открой BoW и проверь «Обновление контента»", 0))
    return out


def check_site():
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20", SITE_URL],
                       capture_output=True, text=True)
    code = r.stdout.strip() or "000"
    if code == "200":
        return []
    return [("site", "Сайт", f"{SITE_URL} снаружи отвечает {code} — смотри logs/vpn-watchdog.log", SITE_CONFIRM)]


def check(now=None):
    now = now or dt.datetime.now(H.tz())
    found = []
    ags = [(a, launchd(a[0], a[1])) for a in agents()]
    ids = launchd_ids([(a[0], a[2], st) for a, st in ags])
    for a, st in ags:
        try:
            p = check_agent(*a, now, st, ids)
        except Exception as e:
            p = ("job:" + _short(a[1]), NAMES.get(_short(a[1]), a[1]), f"проверка упала: {type(e).__name__}: {e}", 0)
        if p:
            found.append(p)
    return found + check_phone(now) + check_site()


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run(now=None, save=True):
    """Проверить, подтвердить по `seen` и сохранить. Возвращает новое состояние."""
    now = now or dt.datetime.now(H.tz())
    stamp = H.iso(now)
    old = _load()
    seen_old = old.get("seen", {})
    since_old = {p["key"]: p["since"] for p in old.get("problems", [])}
    seen, problems = {}, []
    for key, title, detail, confirm in check(now):
        seen[key] = seen_old.get(key, stamp)
        if (now - H.parse_ts(seen[key])).total_seconds() >= confirm:
            problems.append({"key": key, "title": title, "detail": detail, "since": since_old.get(key, stamp)})
    state = {"checkedAt": stamp, "problems": problems, "seen": seen}
    if save:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
        was, now_keys = set(since_old), {p["key"] for p in problems}
        loc = now.strftime("%F %T")
        for p in problems:
            if p["key"] not in was:
                print(f"{loc} + {p['title']}: {p['detail']}", flush=True)
        for k in sorted(was - now_keys):
            print(f"{loc} − {k}", flush=True)
    return state


def current(now=None):
    """Для главной и телефона: сохранённые проблемы плюс «сама самопроверка встала»."""
    now = now or dt.datetime.now(dt.timezone.utc)
    st = _load()
    if not st:
        return {"checkedAt": None, "problems": []}
    problems = list(st.get("problems", []))
    checked = H.parse_ts(st["checkedAt"])
    if (now - checked).total_seconds() > STALE_AFTER:
        problems.append({"key": "selfcheck", "title": "Самопроверка",
                         "detail": f"последний прогон {_when(checked, now.astimezone(H.tz()))} — "
                                   "sh deploy/install-selfcheck.sh на mini", "since": st["checkedAt"]})
    return {"checkedAt": st["checkedAt"], "problems": problems}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "run":
        run()
    elif cmd == "show":
        print(json.dumps(run(save=False), ensure_ascii=False, indent=2))
    elif cmd == "status":
        print(json.dumps(current(), ensure_ascii=False, indent=2))
    else:
        sys.exit(__doc__)
