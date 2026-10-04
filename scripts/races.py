#!/usr/bin/env python3
"""Советы по стартам: трейл, лыжи коньком, открытая вода, триатлон, бег, велосипед — рядом с Москвой или
там, куда стоит ехать ради места. Данные — races.json.

Usage:
  races.py fetch                    # скачать календари серий в data/races/<id>.txt
  races.py extract-prompt           # тексты страниц, изменившихся с прошлого разбора (пусто — разбирать нечего)
  races.py events --file ans.json   # проверить и принять старты, которые модель вынула из страниц
  races.py digest                   # то, что видит модель при выборе: я, мои старты, кандидаты по номерам
  races.py due                      # код 0, если пора подбирать (для races.sh)
  races.py apply --file ans.json [--model opus] [--keep 3]
  races.py verdict <id> liked|dismissed [--dry-run]
  races.py list | calendar

Правило то же, что у статей: модель не называет ни дат, ни ссылок сама. Старт с правдоподобной, но
несуществующей датой хуже выдуманного фильма — на него покупают билеты. Поэтому два прохода:

  1. Разбор. Скрипт сам скачивает страницу-календарь каждой серии (SERIES), модель вынимает из её текста
     старты — и к каждому обязана приложить `evidence`, кусок текста страницы слово в слово. Скрипт ищет
     этот кусок на странице, проверяет, что в нём стоят день и месяц даты, а ссылку принимает, только если
     она есть на той же странице (иначе — страница серии). Разбирается только изменившаяся страница.
  2. Выбор. Проверенные старты идут в дайджест пронумерованными, модель выбирает НОМЕР — как у статей.

«Хочу» заводит старт в Яндекс.Календарь событием на весь день. Дальше его подхватывает load.py: заметка
о здоровье знает старт, ставит его в нагрузку и накануне говорит про отбой и погоду.
Совет живёт ITEM_DAYS, а не неделю, как фильм: на старт решаются дольше, чем на кино, — но не дольше,
чем до конца LEAD_DAYS перед стартом (за неделю до старта уже поздно).
"""
import argparse, datetime, hashlib, html, json, os, re, sys, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "races.json")
PAGES = os.path.join(ROOT, "data", "races")        # тексты страниц: данные машины (.gitignore, deploy.sh)
PROFILE = os.path.join(ROOT, "races", "PROFILE.md")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
VERDICTS = ("liked", "dismissed")
ANSWERED = VERDICTS
ITEM_DAYS = 21          # сколько висит неотвеченный совет
LEAD_DAYS = 10          # ближе к старту совет уже не даётся и снимается: не успеть ни зарегистрироваться, ни собраться
AHEAD_DAYS = 90         # советую только то, что в ближайшие три месяца: февральская Карелия в сентябре — «слишком не скоро»
                        # (29.09.2026); лыжи сами войдут в окно, когда сезон приблизится
HORIZON_DAYS = 400      # дальше этого старты не берём — это расписание позапрошлого года с неверным годом
EVERY_HOURS = 20
PAGE_EVERY_DAYS = 6     # страницы серий перечитываются раз в неделю: календари меняются редко
PAGE_CHARS = 18000      # столько текста страницы уходит модели на разбор
BATCH_CHARS = 70000     # столько за один вызов модели (см. pending_pages)
HISTORY_MAX = 200
UNSEEN_COOLDOWN = 45    # не увиденный совет может вернуться через столько дней
# Дальняя поездка (Алтай, Кавказ, Карелия…) — изредка и одна: её планируют за полгода, поэтому горизонт
# длиннее, но в списке она живёт не чаще одной на TRIP_GAP_DAYS (просьба пользователя 04.10.2026).
TRIP_AHEAD_DAYS = 330   # до конца следующего лета: горный сезон — июнь–август
TRIP_GAP_DAYS = 21
TRIP_MAX_DAYS = 3       # фестиваль длиннее — не «съездить на выходные с перелётом», а отпуск (Большая Чуйская тропа)
TRIP_MAX_KM = 25        # у бегового старта в дальней поездке должна быть дистанция не длиннее (14 км AUT-XS — да)
FAR = re.compile(r"(?i)алта|манжерок|белокурих|телецк|чемал|байкал|бурят|карел|кавказ|эльбрус|приэльбрус|архыз|"
                 r"домбай|кабардин|адыге|красная поляна|роза хутор|сочи|крым|бахчисара|урал|хибин|кольск|мурманск|"
                 r"камчат|сахалин|шерегеш|кузбасс|алтай|иссык|сибир|хакас|тыва|ергаки|дагестан|осети")

SPORT_RU = {"trail": "трейл", "run": "бег", "ski": "лыжи", "swim": "открытая вода", "tri": "триатлон",
            "bike": "велосипед"}
# Слово, по которому load.py узнаёт вид спорта в названии события календаря (его SPORTS).
SPORT_WORD = {"trail": "трейл", "run": "забег", "ski": "лыжная гонка", "swim": "заплыв", "tri": "триатлон",
              "bike": "велогонка"}



# Агрегаторы отдают старты готовыми строками, и модель им не нужна: разбирает их код (STRUCTURED).
# Формат строки общий: «дата | название | место | дистанции | вид ⟨ссылка⟩», дата ДД.ММ.ГГГГ или диапазон.
RR_SPORT = {"run": "run", "trail": "trail", "cycling": "bike", "ski-race": "ski", "triathlon": "tri", "swimming": "swim"}
TRAILISH = re.compile(r"(?i)трейл|trail|горн|скайран|sky ?run|ультра|ultra")


def row(when_, name, place, dist, sport, url, size=""):
    """`size` — чем агрегатор меряет масштаб старта (зарегистрировано, рейтинг): без него модель не отличала
    районный пробег от события, ради которого едут (Трейл Кутузов, 29.09.2026)."""
    return " | ".join([when_, " ".join(str(name or "").split()), " ".join(str(place or "").split()),
                       " ".join(str(dist or "").split()), sport, size or "—"]) + f" ⟨{url}⟩"


def russiarunning(_raw=None):
    """RussiaRunning отдаёт календарь JSON-ом без ключа (POST, не больше 100 за раз). Превращаем его в строки
    текста того же вида, что у остальных страниц, — дальше всё идёт одной дорогой через разбор и проверку.
    Тренировки, онлайн и короткое (всё меньше 10 км у бега) выбрасываются здесь: их там половина."""
    out, skip = [], 0
    while True:
        body = json.dumps({"Language": "ru", "Filter": {"EventsLoaderType": 0}, "Page": {"Skip": skip, "Take": 100}}).encode()
        req = urllib.request.Request("https://reg.russiarunning.com/api/events/list", data=body,
                                     headers={"User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        for e in d.get("list") or []:
            title = " ".join((e.get("title") or "").split())
            if re.search(r"(?i)тренировк|онлайн|online|виртуальн|детск", title):
                continue
            km = sorted({i.get("distance") for i in e.get("raceItems") or [] if isinstance(i.get("distance"), (int, float)) and i.get("distance") > 0})
            if e.get("disciplineCode") == "run" and (not km or km[-1] < 10):
                continue
            b, en = (e.get("beginDate") or "")[:10], (e.get("endDate") or "")[:10]
            try:
                when_ = datetime.date.fromisoformat(b).strftime("%d.%m.%Y")
                if en and en != b:
                    when_ += " – " + datetime.date.fromisoformat(en).strftime("%d.%m.%Y")
            except ValueError:
                continue
            dist = ", ".join(f"{x:g} км" for x in km)
            sport = RR_SPORT.get(e.get("disciplineCode"))
            if not sport:
                continue
            if sport == "run" and TRAILISH.search(title):
                sport = "trail"
            n = e.get("participantsCount") or 0
            out.append(row(when_, title, e.get("place"), dist, sport, f"https://reg.russiarunning.com/event/{e.get('code')}",
                           f"зарегистрировано {n}" if n else ""))
        skip += 100
        if skip >= min(d.get("totalCount") or 0, 500):
            break
    return "\n".join(out)


def probeg(raw):
    """ПроБЕГ: таблица, строка на забег — дата, название, город, дистанции, сайт."""
    rows = []
    for tr in re.findall(r"(?is)<tr\b.*?</tr>", raw):
        if "/event/" not in tr:
            continue
        cells = [" ".join(html.unescape(re.sub(r"(?s)<[^>]+>", " ", c)).split())
                 for c in re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", tr)]
        site = re.search(r'href="(https?://[^"]+)"[^>]*>\s*Сайт', tr)
        ev = re.search(r'href="(/event/\d+/)"', tr)
        url = site.group(1) if site else f"https://probeg.org{ev.group(1)}"
        if len(cells) < 5:
            continue
        # В ячейке названия после него идут «Планирует участвовать…» и рейтинг — отрезаем.
        name = re.split(r"\s+Планиру|\s+\d(?:[.,]\d)?, число оценивших", cells[2])[0]
        rate = re.search(r"(\d(?:[.,]\d)?), число оценивших — (\d+)", cells[2])
        size = f"рейтинг ПроБЕГа {rate.group(1)} ({rate.group(2)} оценок)" if rate else ""
        when_ = cells[1].replace("–", ".").split(".")
        # «03–04.10.2026» → «03.10.2026 – 04.10.2026»
        if len(when_) == 4:
            when_ = f"{when_[0]}.{when_[2]}.{when_[3]} – {when_[1]}.{when_[2]}.{when_[3]}"
        else:
            when_ = cells[1]
        rows.append(row(when_, name, cells[3], cells[4], "trail" if TRAILISH.search(name) else "run", url, size))
    return "\n".join(rows)


def russialoppet(raw):
    """Russialoppet: 54 тыс. знаков страницы — это меню и архив, а календарь — блоки «Название ⟨ссылка⟩ /
    дата / округ, область, город / 50/25 км СВ». Оставляем только блоки со свободным стилем (СВ): классика мне
    не нужна, а без неё календарь в разы короче."""
    lines = page_text(raw, "https://russialoppet.ru/events/").splitlines()
    out, seen = [], set()
    for i, line in enumerate(lines):
        m = re.fullmatch(r"([^⟨]+?) ⟨(https://russialoppet\.ru/events/[\w-]+\d{4}\w*\.html)⟩", line)
        if not m or m.group(1).strip() == "" or m.group(2) in seen:
            continue
        date = dist = None
        place = []
        for nxt in lines[i + 1:i + 10]:
            if date is None:
                if re.fullmatch(r"\d\d\.\d\d\.\d{4}", nxt):
                    date = nxt
                continue
            if re.search(r"\bкм\b", nxt):
                dist = nxt
                break
            place.append(re.sub(r"⟨[^⟩]*⟩", "", nxt).strip(" ,"))
        if not date or not dist or "СВ" not in dist:
            continue
        seen.add(m.group(2))
        dist = re.sub(r"\s*/\s*", "/", dist.replace("СВ", "")).strip()
        out.append(row(date, m.group(1), ", ".join(x for x in place if x), dist, "ski", m.group(2)))
    return "\n".join(out)


# Серии и страницы, на которых их календарь виден в самом HTML (без JS). Проверено 29.09.2026 с ноутбука
# и с mini. Новую серию добавлять сюда же; если сайт рисует календарь скриптом — искать JSON, который он
# зовёт (так устроен RussiaRunning). Не работают и потому не здесь: Московский марафон, L'Étape, Казанский
# марафон (не отвечают зарубежным IP, а mini ходит через VPN — их старты приходят через RussiaRunning и
# ПроБЕГ), Титан (календарь не обновлялся с 2025-го), Лига Героев (гонки с препятствиями, не мой спорт).
SERIES = [
    {"id": "wildtrail", "name": "Wild Trail", "sports": ["trail"], "url": "https://wildtrail.ru/",
     "hint": "у каждого события на странице есть «Место: …» и «Дата: …»"},
    {"id": "grut", "name": "Golden Ring Ultra Trail", "sports": ["trail"], "url": "https://goldenultra.ru/grut/",
     "place": "Суздаль"},
    {"id": "ultra100", "name": "Ultra 100 (Эльтон)", "sports": ["trail"], "url": "https://ultra100.ru/",
     "place": "Волгоградская обл., Эльтон"},
    {"id": "redfox", "name": "Red Fox Elbrus Race", "sports": ["trail"], "url": "https://elbrus.redfox.ru/",
     "place": "Приэльбрусье"},
    {"id": "trails", "name": "Календарь трейлов («Марафонец»)", "sports": ["trail"], "chars": 40000,
     "url": "https://marathonec.ru/kalendar-trejlovyx-zabegov-rossii/",
     "hint": "агрегатор: строка на трейл, дата ДД/ММ/ГГГГ, потом название, дистанции, место; series у всех строк — trails"},
    {"id": "grom", "name": "Гром", "sports": ["ski", "run", "trail", "swim"], "url": "https://grom.place/",
     "place": "Москва", "hint": "клуб Громова: Grom Ski (конёк, Мещерский парк), забеги, трейлы OpenBand, заплывы"},
    {"id": "russialoppet", "name": "Russialoppet", "sports": ["ski"], "url": "https://russialoppet.ru/events/2027/",
     "parse": russialoppet, "structured": True},
    {"id": "ironstar", "name": "IRONSTAR", "sports": ["tri", "swim"], "url": "https://iron-star.com/event/",
     "chars": 30000, "hint": "триатлон (1/8, 1/4, 1/2, полный) и заплывы SWIMSTAR; дистанция старта — в названии"},
    {"id": "xwaters", "name": "X-WATERS", "sports": ["swim"], "url": "https://x-waters.com/events/"},
    {"id": "swimcup", "name": "SwimCup", "sports": ["swim"], "url": "https://swimcup.ru/series/",
     "hint": "год стоит в названии этапа («Крылатское 2027»), а в дате только день и месяц"},
    {"id": "granfondo", "name": "Gran Fondo Russia", "sports": ["bike"], "url": "https://www.granfondo.ru/"},
    {"id": "cyclingrace", "name": "CyclingRace", "sports": ["bike"], "url": "https://cyclingrace.ru/",
     "place": "Москва"},
    {"id": "altaitrail", "name": "Altai Ultra-Trail", "sports": ["trail"], "url": "https://altai-trail.ru/",
     "place": "Республика Алтай",
     "hint": "в меню «Календарь <год>» — все старты команды AUT с датами; у самого AUT дистанции XS 14 км +600 м … XXL "
             "166 км; кэмпы (AUT Camp, Issyk-Kul Camp, Ноябрьская Катунь) — не старты, пропускай"},
    {"id": "taigatrail", "name": "Taiga Trail", "sports": ["trail"], "url": "https://taigatrail.run/",
     "hint": "серия сибирских трейлов команды Altai Ultra-Trail: Манжерок, Шерегеш, Белокуриха, Барангол, Новосибирск; "
             "у каждого старта дата, место и дистанции с набором (D+) — набор пиши в distances"},
    {"id": "russiarunning", "name": "RussiaRunning", "sports": ["run", "trail"], "chars": 60000,
     "url": "https://reg.russiarunning.com/", "fetch": russiarunning, "structured": True},
    {"id": "probeg", "name": "ПроБЕГ, Центр", "sports": ["run", "trail"], "chars": 30000,
     "url": "https://probeg.org/races/?country=district8&date_region=2&hide_parkruns=on&distance_from=10&page=1",
     "parse": probeg, "structured": True},
]


def today():
    return datetime.date.today()


def load():
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            db = json.load(f)
    else:
        db = {}
    for k, v in (("updatedAt", None), ("model", None), ("fetchedAt", None), ("items", []), ("history", []),
                 ("plans", []), ("events", []), ("pages", {})):
        db.setdefault(k, v)
    return db


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


def series(sid):
    return next((s for s in SERIES if s["id"] == sid), None)


def days_to(date):
    try:
        return (datetime.date.fromisoformat(str(date)[:10]) - today()).days
    except ValueError:
        return -10 ** 6


def age_days(when):
    return -days_to(when) if when else 10 ** 6


# ---------------------------------------------------------------- страницы
BLOCK = r"</?(?:p|div|li|tr|td|th|h[1-6]|br|section|article|header|footer|ul|ol|table|dt|dd)\b[^>]*>"


def page_text(raw, base):
    """HTML → текст с переводами строк по блокам и ссылками в угловых скобках: «Карелия ⟨https://…⟩».
    Ссылки остаются в тексте, чтобы модель могла назвать ссылку старта, а скрипт — найти её здесь же."""
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)

    def link(m):
        href = html.unescape(m.group(1)).strip()
        inner = re.sub(r"(?s)<[^>]+>", " ", m.group(2))
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return inner
        return f"{inner} ⟨{urllib.parse.urljoin(base, href)}⟩"
    s = re.sub(r'(?is)<a\b[^>]*?href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', link, s)
    s = re.sub(BLOCK, "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"(?s)<[^>]+>", " ", s))
    lines = [" ".join(l.split()) for l in s.splitlines()]
    out, prev = [], None
    for l in lines:
        if l and l != prev:
            out.append(l)
        prev = l
    return "\n".join(out)


def get(url, timeout=30, tries=3):
    """С mini (VPN в Финляндии) российские хостинги иногда молчат до таймаута — вторая попытка часто проходит."""
    for n in range(tries):
        try:
            return _get(url, timeout)
        except (urllib.error.URLError, OSError):
            if n == tries - 1:
                raise


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                                               "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def chars(s):
    return s.get("chars") or PAGE_CHARS


def page_path(sid):
    return os.path.join(PAGES, f"{sid}.txt")


def read_page(sid):
    try:
        with open(page_path(sid), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def fetch(force=False, verbose=True, only=None):
    db = load()
    os.makedirs(PAGES, exist_ok=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for s in SERIES:
        if only and s["id"] not in only:
            continue
        st = db["pages"].setdefault(s["id"], {})
        if not force and st.get("fetchedAt") and age_days(st["fetchedAt"]) < PAGE_EVERY_DAYS and os.path.exists(page_path(s["id"])):
            continue
        try:
            if s.get("fetch"):
                text = s["fetch"]()
            else:
                raw = get(s["url"])
                text = s["parse"](raw) if s.get("parse") else page_text(raw, s["url"])
        except (urllib.error.URLError, OSError, ValueError) as e:
            st.update(error=f"{type(e).__name__}: {e}"[:200], triedAt=now)
            if verbose:
                print(f"  {s['name']}: не скачалась ({st['error']})", file=sys.stderr)
            continue
        text = text[:chars(s)]
        with open(page_path(s["id"]), "w", encoding="utf-8") as f:
            f.write(text)
        h = hashlib.sha1(text.encode()).hexdigest()[:12]
        changed = h != st.get("extractedHash")
        st.update(fetchedAt=now, hash=h, chars=len(text))
        st.pop("error", None)
        if s.get("structured"):
            evs = structured_events(s, text)
            db["events"] = [e for e in db["events"] if e["series"] != s["id"]] + evs
            st.update(extractedHash=h, events=len(evs))
            changed = False
        if verbose:
            print(f"  {s['name']}: {len(text)} знаков{', изменилась' if changed else ''}")
    db["fetchedAt"] = now
    db["events"].sort(key=lambda e: (e["date"], e["name"]))
    prune(db)
    save(db)
    return db


def pending_pages(db=None, force=False):
    """Страницы, которые ждут разбора моделью, — первой порцией до BATCH_CHARS: на 250 тыс. знаков разом модель
    не дописывает ответ и последние страницы теряет молча. races.sh зовёт разбор, пока порции не кончатся;
    порция считается одинаково в extract-prompt и в events, потому что между ними ничего не меняется."""
    db = db or load()
    todo = [s for s in SERIES if not s.get("structured") and (st := db["pages"].get(s["id"])) and st.get("hash")
            and (force or st["hash"] != st.get("extractedHash")) and read_page(s["id"])]
    batch, size = [], 0
    for s in todo:
        n = len(read_page(s["id"]))
        if batch and size + n > BATCH_CHARS:
            break
        batch.append(s)
        size += n
    return batch


def structured_events(s, text):
    """Строки агрегатора → старты, без модели. Цитата — сама строка, так что проверка та же, что у разбора."""
    out = {}
    for line in text.splitlines():
        m = re.fullmatch(r"(\d\d\.\d\d\.\d{4})(?: – (\d\d\.\d\d\.\d{4}))? \| (.*?) \| (.*?) \| (.*?) \| (\w+)(?: \| (.*?))? ⟨(.*)⟩", line)
        if not m:
            continue
        b, en, name, place, dist, sport, size, url = m.groups()
        iso = lambda d: datetime.date(int(d[6:]), int(d[3:5]), int(d[:2])).isoformat()  # noqa: E731
        try:
            date, end = iso(b), iso(en) if en else None
        except ValueError:
            continue
        if not name or not 0 <= days_to(date) <= HORIZON_DAYS or re.search(r"(?i)тренировк|детск|онлайн", name):
            continue
        if end and (end <= date or (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(date)).days > 7):
            end = None
        e = {"id": event_id(s["id"], name, date), "series": s["id"], "seriesName": s["name"], "name": name,
             "date": date, "dateEnd": end, "place": place or None, "sports": [sport] if sport in SPORT_RU else s["sports"],
             "distances": dist or None, "url": url, "evidence": line[:300],
             "size": size if size and size != "—" else None}
        out[e["id"]] = e
    return list(out.values())


def extract_prompt(force=False):
    todo = pending_pages(force=force)
    if not todo:
        return ""
    out = []
    for s in todo:
        out.append(f"\n===== СТРАНИЦА series={s['id']} | {s['name']} | {', '.join(SPORT_RU[x] for x in s['sports'])} | {s['url']}")
        if s.get("hint"):
            out.append(f"(подсказка: {s['hint']})")
        out.append(read_page(s["id"]))
    return "\n".join(out)


# ---------------------------------------------------------------- проверка разбора
MONTHS = ["январ|янв", "феврал|фев", "март|мар", "апрел|апр", "ма[йя]|мая", "июн", "июл", "август|авг",
          "сентябр|сен", "октябр|окт", "ноябр|ноя", "декабр|дек"]
MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def norm(s):
    return " ".join(str(s or "").lower().replace("ё", "е").replace(" ", " ").split())


def date_in(evidence, date):
    """Стоят ли в куске текста день и месяц даты: «14 февраля», «14.02», «13–14 февраля», «14-15.02.2027»."""
    ev = norm(evidence)
    d = datetime.date.fromisoformat(date)
    if not re.search(rf"(?<!\d)0?{d.day}(?!\d)", ev):
        return False
    return bool(re.search(rf"(?<!\d)0?{d.day}\s*[./]\s*0?{d.month}(?!\d)", ev)
                or re.search(rf"(?<!\d)0?{d.month}(?!\d)", ev) and re.search(r"\d{1,2}\s*[./]\s*\d{1,2}", ev)
                or re.search(MONTHS[d.month - 1], ev) or MONTHS_EN[d.month - 1] in ev)


def event_id(sid, name, date):
    return hashlib.sha1(f"{sid}|{norm(name)}|{date}".encode()).hexdigest()[:10]


def parse_json_list(text):
    text = re.sub(r"```[a-z]*", "", text)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < i:
        raise SystemExit("в ответе модели нет JSON-массива")
    data = json.loads(text[i:j + 1])
    return [c for c in data if isinstance(c, dict)]


def accept_events(text, force=False, verbose=True):
    """Разбор модели → проверенные старты. Страницы, которые были в разборе, получают свой набор целиком
    (старый набор серии заменяется), поэтому старт, снятый с календаря, уходит и отсюда."""
    db = load()
    todo = {s["id"] for s in pending_pages(db, force)}
    got, bad = {}, []
    for c in parse_json_list(text):
        sid = c.get("series")
        s = series(sid)
        if not s or sid not in todo:
            bad.append(f"чужая серия {sid!r}")
            continue
        page = read_page(sid)
        name, date, ev = (c.get("name") or "").strip(), str(c.get("date") or "")[:10], c.get("evidence") or ""
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            bad.append(f"{name}: дата {date!r}")
            continue
        if not name or not 0 <= days_to(date) <= HORIZON_DAYS:
            bad.append(f"{name}: {date} вне окна")
            continue
        if len(norm(ev)) < 6 or norm(ev) not in norm(page):
            bad.append(f"{name}: evidence не найден на странице")
            continue
        if not date_in(ev, date):
            bad.append(f"{name}: в evidence нет {date}")
            continue
        url = (c.get("url") or "").strip()
        if not url or url not in page:
            url = s["url"]
        end = str(c.get("dateEnd") or "")[:10] or None
        if end and not (re.fullmatch(r"\d{4}-\d\d-\d\d", end) and date <= end and days_to(end) - days_to(date) <= 7):
            end = None
        sports = [x for x in (c.get("sports") or []) if x in SPORT_RU] or s["sports"]
        e = {"id": event_id(sid, name, date), "series": sid, "seriesName": s["name"], "name": name, "date": date,
             "dateEnd": end, "place": (c.get("place") or "").strip() or s.get("place"), "sports": sports,
             "distances": (c.get("distances") or "").strip() or None, "url": url, "evidence": ev.strip()[:300]}
        got.setdefault(sid, {})[e["id"]] = e
    keep = [e for e in db["events"] if e["series"] not in todo]
    for sid in todo:
        keep += list(got.get(sid, {}).values())
        st = db["pages"][sid]
        st["extractedHash"] = st.get("hash")
        st["events"] = len(got.get(sid, {}))
    db["events"] = sorted(keep, key=lambda e: (e["date"], e["name"]))
    prune(db)
    save(db)
    if verbose:
        for sid in sorted(todo):
            print(f"  {series(sid)['name']}: {len(got.get(sid, {}))} стартов")
        for b in bad:
            print(f"  отброшено — {b}", file=sys.stderr)
    return db["events"]


def prune(db):
    db["events"] = [e for e in db["events"] if days_to(e["date"]) >= 0]


# ---------------------------------------------------------------- советы
def history_entry(r):
    return {"id": r["id"], "title": r.get("title"), "date": r.get("date"), "series": r.get("series"),
            "sports": r.get("sports"), "far": r.get("far"), "reason": r.get("reason"), "note": r.get("note"), "verdict": r.get("verdict") or "new",
            "at": r.get("decidedAt") or r.get("suggestedAt") or today().isoformat(), "suggestedAt": r.get("suggestedAt")}


def expire(db):
    """Неотвеченное дольше ITEM_DAYS или ближе LEAD_DAYS к старту уезжает в историю как «не увидел»."""
    live, gone = [], []
    for r in db["items"]:
        (live if age_days(r.get("suggestedAt")) < ITEM_DAYS and days_to(r["date"]) >= LEAD_DAYS else gone).append(r)
    for r in gone:
        r["verdict"], r["decidedAt"] = "unseen", today().isoformat()
    if gone:
        db["history"] = ([history_entry(r) for r in gone] + db["history"])[:HISTORY_MAX]
        db["items"] = live
    return live


def excluded(db):
    """Что не предлагать: живое, запланированное, отвеченное — навсегда; не увиденное — UNSEEN_COOLDOWN дней."""
    out = {r["id"] for r in db["items"]} | {p["id"] for p in db["plans"]}
    for h in db["history"]:
        if h.get("verdict") in ANSWERED or age_days(h.get("at")) < UNSEEN_COOLDOWN:
            out.add(h["id"])
    return out


STOP = {"забег", "трейл", "trail", "марафон", "полумарафон", "фестиваль", "бега", "серия", "этап", "кубок", "open",
        "race", "run", "лыжный", "заплыв", "триатлон", "гонка"}
# Серии раньше агрегаторов: у страницы серии ссылка и дистанции точнее, чем у перепечатки.
AGGREGATORS = ("trails", "russiarunning", "probeg")


def words(name):
    return {w for w in re.findall(r"[a-zа-я0-9]+", norm(name)) if len(w) >= 4 and w not in STOP and not re.fullmatch(r"20\d\d", w)}


def dedupe(events):
    """Один старт с трёх сайтов — одна строка: в тот же день и слова одного названия входят в другое."""
    out = []
    for e in sorted(events, key=lambda e: e["series"] in AGGREGATORS):
        w = words(e["name"])
        if any(o["date"] == e["date"] and w and (ow := words(o["name"])) and (w <= ow or ow <= w) for o in out):
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e["date"], e["name"]))


# Плавание — только открытая вода: заплыв в бассейне («Индор» SwimCup) отвергнут 29.09.2026.
INDOOR = re.compile(r"(?i)индор|indoor|бассейн|\bpool\b|зимн\w* заплыв в бассейне")


def indoor(e):
    """Заплыв в бассейне: по названию, а зимой (ноябрь–апрель) — любой, кроме зимнего плавания в проруби.
    SwimCup зовёт свои зимние старты «Спринт Ноябрь», «Минуты Москва Декабрь» — слова «бассейн» там нет,
    а открытой воды в Москве в декабре не бывает. Египет и Сочи — открытая вода круглый год."""
    if "swim" not in e["sports"] or "tri" in e["sports"]:
        return False
    text = f"{e['name']} {e.get('place') or ''}"
    if INDOOR.search(text):
        return True
    warm = re.search(r"(?i)египет|шарм|хургад|сочи|сириус|оаэ|дубай|турци|таиланд|мальдив", text)
    ice = re.search(r"(?i)лед|лёд|прорубь|моржев|зимнее плавание|ice", text)
    return int(e["date"][5:7]) in (11, 12, 1, 2, 3, 4) and not warm and not ice


def far(e):
    """Старт, ради которого летят: регион из FAR в месте или названии."""
    return bool(FAR.search(f"{e['name']} {e.get('place') or ''} {e.get('seriesName') or ''}"))


def kms(text):
    """Все дистанции в км: «14, 36, 46 км» — это три, а не одна; набор («+600 м») и метры («2500 м») не в счёт."""
    t = re.sub(r"\+\s*\d+\s*м\b|\(\s*\+[^)]*\)", " ", text or "")
    return [float(x.replace(",", ".")) for x in re.findall(r"(?<![\d.,])(\d+(?:[.,]\d+)?)(?!\s*м\b)(?!\d|[.,]\d)", t)]


def trip_ok(e):
    """Дальняя поездка, которую можно сделать: не дольше TRIP_MAX_DAYS и с посильной дистанцией.
    Дистанции не указаны — решает модель по правилам промпта."""
    if e.get("dateEnd") and (datetime.date.fromisoformat(e["dateEnd"]) - datetime.date.fromisoformat(e["date"])).days >= TRIP_MAX_DAYS:
        return False
    if {"trail", "run"} & set(e["sports"]):
        d = [k for k in kms(e.get("distances")) if k >= 5]
        return not d or min(d) <= TRIP_MAX_KM
    return True


def trip_slot(db):
    """Можно ли сейчас советовать дальнюю поездку: живой нет и прошлая была не раньше TRIP_GAP_DAYS назад."""
    if any(r.get("far") for r in db["items"]):
        return False
    last = [h.get("suggestedAt") for h in db["history"] if h.get("far") and h.get("suggestedAt")]
    return not last or age_days(max(last)) >= TRIP_GAP_DAYS


def candidates(db):
    ex = excluded(db)
    ex |= {e["id"] for e in db["events"] if indoor(e)}
    slot = trip_slot(db)
    out = []
    for e in db["events"]:
        if e["id"] in ex or days_to(e["date"]) < LEAD_DAYS:
            continue
        if far(e):
            # дальнее — только когда свободно место поездки, зато на горизонт до TRIP_AHEAD_DAYS
            if slot and trip_ok(e) and days_to(e["date"]) <= TRIP_AHEAD_DAYS:
                out.append(e)
        elif days_to(e["date"]) <= AHEAD_DAYS:
            out.append(e)
    return dedupe(out)


def when(e):
    d = datetime.date.fromisoformat(e["date"])
    s = d.strftime("%d.%m.%Y") + " " + ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d.weekday()]
    if e.get("dateEnd") and e["dateEnd"] != e["date"]:
        s += "–" + datetime.date.fromisoformat(e["dateEnd"]).strftime("%d.%m")
    return s


def busy_days(db):
    """Что уже стоит в календаре на дни стартов: чужие и свои дела на выходных — то, из-за чего старт не случится.
    Работа не в счёт (старты по выходным), календарь без сети — просто пусто."""
    try:
        import schedule as S
        horizon = max([days_to(e["date"]) for e in db["events"]] + [0]) + 2
        ag = S.agenda(min(horizon, HORIZON_DAYS), skip=S.hidden_calendars())
    except Exception:
        return {}
    out = {}
    for date, evs in ag.items():
        # Правила (работа, ипотека десятого числа, тренировки по будням) старту не мешают — мешает разовое.
        names = [(f"{e['who']}: " if e.get("who") else "") + e["summary"] for e in evs if not e.get("recurring")]
        if names:
            out[date.isoformat()] = names
    return out


def profile_text():
    try:
        with open(PROFILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def context_sections():
    """Цель, форма и нагрузка — то же, что видит заметка о здоровье. Без health.db (ноутбук) секции выпадают."""
    out = []
    try:
        import context as C
    except Exception:
        return out
    # Таблица дней (C.body) сюда не идёт: для выбора старта на весну нужна форма, а не вчерашний сон.
    for head, fn in (("## Мои цели", C.goals), ("## Нагрузка и недавние старты", C.load_)):
        try:
            lines = fn()
        except Exception:
            continue
        if lines:
            out.append("\n".join(["", head] + lines))
    try:
        import vitals as V
        form = V.digest().strip()
        if form:
            out.append("\n## Форма по часам (темп, VO2max, вес)\n" + form)
    except Exception:
        pass
    return out


def digest():
    db = load()
    expire(db)
    prune(db)
    save(db)
    out = []
    p = profile_text()
    if p:
        out.append(p)
    out += context_sections()

    if db["plans"]:
        out.append("\n## Уже решил ехать (взял из твоих прошлых советов)")
        for r in db["plans"]:
            out.append(f"- {when(r)} {r['title']} ({', '.join(SPORT_RU[x] for x in r.get('sports') or [])})")
    if db["items"]:
        out.append("\n## Твои советы, которые сейчас висят у меня (не повторяй)")
        for r in db["items"]:
            out.append(f"- {when(r)} {r['title']}")
    real = [h for h in db["history"] if h.get("verdict") in ANSWERED]
    if real:
        out.append("\n## Вердикты по прошлым советам-стартам (свежие сверху)")
        for h in real[:40]:
            word = {"liked": "ВЗЯЛ", "dismissed": "НЕ ТО"}[h["verdict"]]
            out.append(f"- {word}: {h.get('title')} ({h.get('date')}) — {h.get('reason') or ''}"
                       + (f" / МОЯ ПРИЧИНА: {h['note']}" if h.get("note") else ""))
    unseen = sum(1 for h in db["history"] if h.get("verdict") not in ANSWERED)
    if unseen:
        out.append(f"\nЕщё {unseen} советов я не увидел — это не отказ, а пропуск.")

    busy = busy_days(db)
    cands = candidates(db)
    out.append(f"\n## Кандидаты: старты с календарей серий, сегодня {today():%d.%m.%Y}. Выбирай ТОЛЬКО из них, по номеру.")
    for n, e in enumerate(cands, 1):
        line = (f"{n}. {when(e)} (через {days_to(e['date'])} дн.) · {e['seriesName']}: {e['name']}"
                f" · {', '.join(SPORT_RU[x] for x in e['sports'])}")
        if e.get("place"):
            line += f" · {e['place']}"
        if e.get("distances"):
            line += f" · дистанции: {e['distances']}"
        if e["series"] not in AGGREGATORS:
            line += " · ИЗВЕСТНАЯ СЕРИЯ"
        if far(e):
            line += " · ДАЛЬНЯЯ ПОЕЗДКА"
        elif e.get("size"):
            line += f" · {e['size']}"
        clash = [x for d in {e["date"], e.get("dateEnd") or e["date"]} for x in busy.get(d, [])]
        if clash:
            line += f" · В КАЛЕНДАРЕ В ЭТОТ ДЕНЬ: {'; '.join(dict.fromkeys(clash))}"
        out.append(line)
    if not cands:
        out.append("(кандидатов нет)")
    if not trip_slot(db):
        out.append("\nДальнюю поездку в этот раз не советуй: одна уже висит или была недавно.")
    return "\n".join(out)


def apply_answer(text, model=None, keep=3):
    db = load()
    live = expire(db)
    need = keep - len(live)
    if need <= 0:
        save(db)
        print("  все места заняты живыми советами — ничего не меняю")
        return []
    cands = candidates(db)
    items = []
    for c in parse_json_list(text):
        if len(items) >= need:
            break
        try:
            n = int(c.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(cands):
            print(f"  номер вне списка: {c.get('n')}")
            continue
        e = cands[n - 1]
        if any(i["id"] == e["id"] for i in items):
            continue
        if far(e) and (any(i.get("far") for i in items) or any(r.get("far") for r in live)):
            print(f"  вторая дальняя поездка за раз — пропускаю: {e['name']}")
            continue
        r = dict(e, title=title(e),
                 distance=(c.get("distance") or "").strip() or None, trip=(c.get("trip") or "").strip() or None,
                 reason=(c.get("why") or "").strip() or None, suggestedAt=today().isoformat(), verdict="new",
                 far=far(e))
        r["meta"] = meta(r)
        items.append(r)
        print(f"  + {when(r)} {r['title']}")
    if not items:
        save(db)
        raise SystemExit("модель не выбрала ни одного старта из списка")
    db["items"] = live + items
    db["updatedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    db["model"] = model or db.get("model")
    save(db)
    return items


def title(e):
    """«Wild Trail: Архыз», но у агрегатора — только сам старт: «ПроБЕГ, Центр: Трейл Кутузов» — не название."""
    if e["series"] in AGGREGATORS + ("russialoppet",) or norm(e["seriesName"]) in norm(e["name"]):
        return e["name"]
    return f"{e['seriesName']}: {e['name']}"


MONTH_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
             "ноября", "декабря"]


def human_date(e):
    d = datetime.date.fromisoformat(e["date"])
    s = f"{d.day} {MONTH_GEN[d.month - 1]}"
    if e.get("dateEnd") and e["dateEnd"] != e["date"]:
        d2 = datetime.date.fromisoformat(e["dateEnd"])
        s = (f"{d.day}–{d2.day} {MONTH_GEN[d.month - 1]}" if d2.month == d.month
             else f"{s} – {d2.day} {MONTH_GEN[d2.month - 1]}")
    return s + (f" {d.year}" if d.year != today().year else "")


def meta(r):
    """Подпись карточки: когда · где · что бежать. Дистанция — та, что советует модель, иначе все с календаря."""
    return " · ".join(filter(None, [human_date(r), r.get("place"), r.get("distance") or r.get("distances"),
                                     r.get("trip")]))


# ---------------------------------------------------------------- календарь
def calendar_title(r):
    """Название события такое, чтобы load.py узнал в нём и старт (RACE), и вид спорта (SPORTS)."""
    t = r["title"]
    word = SPORT_WORD[r["sports"][0]] if r.get("sports") else "старт"
    if not re.search(r"(?i)трейл|trail|забег|марафон|лыжн|заплыв|swim|триатлон|iron|вело|gran ?fondo|гонк", t):
        t = f"{t} ({word})"
    if r.get("distance"):
        t += f", {r['distance']}"
    return t


def add_to_calendar(r, dry_run=False):
    """Событие на весь день (или на все дни фестиваля) в мой календарь. Время старта на странице серии
    есть не всегда, а весь день — честно: в этот день я занят стартом."""
    import uuid
    import schedule as S
    start = datetime.date.fromisoformat(r["date"])
    end = datetime.date.fromisoformat(r.get("dateEnd") or r["date"]) + datetime.timedelta(days=1)
    uid = f"{uuid.uuid4()}@bodywithoutorgans.cc"
    desc = S.ical_escape("\n".join(filter(None, [r.get("url"), r.get("place"), r.get("distances")])))
    ics = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//bodywithoutorgans//races.py//RU", "BEGIN:VEVENT",
                       f"UID:{uid}", f"DTSTAMP:{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}",
                       f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                       f"SUMMARY:{S.ical_escape(calendar_title(r))}", f"DESCRIPTION:{desc}",
                       f"URL:{r.get('url') or ''}", "TRANSP:OPAQUE", "END:VEVENT", "END:VCALENDAR"]) + "\r\n"
    if dry_run:
        return {"uid": uid, "title": calendar_title(r), "dry": True}
    only = S.only_calendars() or []
    cals = S.calendars()
    mine = next((c for c in cals if c["name"] in only), None) or cals[0]
    S.put_event(mine["href"].rstrip("/") + f"/{uid}.ics", ics)
    try:
        S.fetch(force=True)
    except Exception:
        pass
    return {"uid": uid, "title": calendar_title(r), "calendar": mine["name"]}


def verdict(rid, v, dry_run=False, note=None):
    if v not in VERDICTS:
        raise SystemExit(f"bad verdict: {v}")
    db = load()
    r = next((x for x in db["items"] if x["id"] == rid), None)
    if not r:
        raise SystemExit(f"нет такого совета: {rid}")
    added = None
    if v == "liked":
        try:
            cal = add_to_calendar(r, dry_run)
        except Exception as e:      # календарь недоступен — план всё равно запоминаем, событие можно завести руками
            cal = {"error": f"{type(e).__name__}: {e}"[:200]}
        if dry_run:
            return {"calendar": cal}
        db["plans"] = sorted([dict(r, plannedAt=today().isoformat(), calendar=cal)]
                             + [p for p in db["plans"] if p["id"] != rid], key=lambda p: p["date"])
        added = {"id": rid, "title": r["title"], "date": r["date"], "calendar": cal}
    r["verdict"], r["decidedAt"] = v, today().isoformat()
    if note:
        r["note"] = note
    db["items"] = [x for x in db["items"] if x["id"] != rid]
    db["history"] = ([history_entry(r)] + db["history"])[:HISTORY_MAX]
    save(db)
    return {"rec": r, "added": added}


def due(keep=3):
    db = load()
    live = expire(db)
    save(db)
    free = keep - len(live)
    if free <= 0:
        return False, f"все {keep} места заняты"
    if not db.get("updatedAt"):
        return True, "ни разу не запускался"
    hours = (datetime.datetime.now() - datetime.datetime.fromisoformat(db["updatedAt"])).total_seconds() / 3600
    if hours >= EVERY_HOURS:
        return True, f"прошло {hours:.0f} ч, свободных мест {free}"
    return False, f"прошло {hours:.1f} ч"


def inbox_item():
    """Самый старый живой совет — для инбокса на полке главной (его смешивает с остальными serve.py)."""
    db = load()
    live = expire(db)
    save(db)
    if not live:
        return None, 0
    live.sort(key=lambda r: r.get("suggestedAt") or "")
    r = live[0]
    return ({"kind": "race", "id": r["id"], "title": r["title"], "meta": r.get("meta") or meta(r),
             "reason": r.get("reason"), "url": r.get("url") or "health.html#recs", "eyebrow": "Старт",
             "acts": list(VERDICTS), "days": age_days(r.get("suggestedAt")),
             "suggestedAt": r.get("suggestedAt")}, len(live))


def public():
    """То, что отдаётся странице: советы и планы, без кэша страниц и кандидатов."""
    db = load()
    expire(db)
    prune(db)
    save(db)
    for r in db["items"]:
        r["meta"] = meta(r)
    plans = [p for p in db["plans"] if days_to(p.get("dateEnd") or p["date"]) >= 0]
    return {"updatedAt": db["updatedAt"], "model": db["model"], "items": db["items"],
            "plans": [{"id": p["id"], "title": p["title"], "date": p["date"], "meta": meta(p), "url": p.get("url")}
                      for p in plans],
            "events": len(db["events"])}


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--force", action="store_true")
    f.add_argument("--only", nargs="*")
    e = sub.add_parser("extract-prompt")
    e.add_argument("--force", action="store_true")
    ev = sub.add_parser("events")
    ev.add_argument("--file", required=True)
    ev.add_argument("--force", action="store_true")
    sub.add_parser("digest")
    sub.add_parser("due")
    sub.add_parser("list")
    sub.add_parser("calendar")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=3)
    v = sub.add_parser("verdict")
    v.add_argument("id")
    v.add_argument("verdict", choices=VERDICTS)
    v.add_argument("--dry-run", action="store_true")
    v.add_argument("--note", help="почему — уходит в следующий промпт")
    d = sub.add_parser("drop", help="снять совет без вердикта (не «не то», а «не сейчас»)")
    d.add_argument("id")
    args = ap.parse_args()

    read = lambda p: sys.stdin.read() if p == "-" else open(p, encoding="utf-8").read()  # noqa: E731
    if args.cmd == "fetch":
        db = fetch(args.force, only=args.only)
        print(f"страниц к разбору: {len(pending_pages(db))}")
    elif args.cmd == "extract-prompt":
        print(extract_prompt(args.force), end="")
    elif args.cmd == "events":
        print(f"стартов в кэше: {len(accept_events(read(args.file), args.force))}")
    elif args.cmd == "digest":
        print(digest())
    elif args.cmd == "due":
        ok, why = due()
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "apply":
        print(f"{len(apply_answer(read(args.file), args.model, args.keep))} стартов сохранено в races.json")
    elif args.cmd == "verdict":
        print(json.dumps(verdict(args.id, args.verdict, args.dry_run, args.note), ensure_ascii=False, indent=2)[:600])
    elif args.cmd == "drop":
        db = load()
        db["items"] = [x for x in db["items"] if x["id"] != args.id]
        save(db)
    elif args.cmd == "calendar":
        db = load()
        for e in db["events"]:
            print(f"{when(e)}  {e['seriesName']}: {e['name']}  [{', '.join(e['sports'])}]  {e.get('place') or ''}"
                  f"  {e.get('distances') or ''}")
        for sid, st in sorted(db["pages"].items()):
            if st.get("error"):
                print(f"  ! {sid}: {st['error']}")
    elif args.cmd == "list":
        db = load()
        print(f"обновлено {db.get('updatedAt')} ({db.get('model')}), стартов в кэше {len(db['events'])}, "
              f"кандидатов {len(candidates(db))}")
        for r in db["items"]:
            print(f"  {r['id']}  {meta(r)}  {r['title']}\n      {r.get('reason') or ''}\n      {r.get('url')}")
        for p in db["plans"]:
            print(f"  план: {when(p)} {p['title']}")


if __name__ == "__main__":
    main()
