#!/usr/bin/env python3
"""Французский: материалы, тесты и карточки. Раз в день (и сразу, как только я закончил прошлые)
mini ищет короткие французские видео и статьи, Claude выбирает из них три под мой вкус и уровень,
а потом по НАСТОЯЩЕЙ расшифровке видео (субтитры) или тексту статьи собирает разбор: слова и
маленький тест. Ответы запоминаются: ошибки идут в следующий промпт, слова — в карточки
с интервальным повторением. Данные — french.json.

Usage:
  french.py fetch [--days 21]            # кандидаты: ленты + поиск YouTube → french.json (cache)
  french.py digest                       # первый проход: что видит модель, когда выбирает материалы
  french.py apply --file answer.json     # её выбор → материалы (скачивает субтитры/текст)
  french.py lesson-digest                # второй проход: тексты материалов, по которым собирается разбор
  french.py lessons --file lesson.json   # разборы (слова + тест) → материалам
  french.py drill-digest                 # третий проход: что видит модель, когда пишет урок и перевод
  french.py drills --file drills.json    # урок на тему + перевод → материалам
  french.py due                          # код 0, если пора искать новое (для french.sh)
  french.py list | cards | stats
  french.py finish <id> [--verdict done|dismissed]   # закрыть материал руками
  french.py level B1

Почему так, а не «модель сама придумает урок»: выдуманная ссылка — её любимая ошибка (ровно как
выдуманные фильмы, пока их не начали проверять в OMDb), поэтому материал всегда настоящий и
выбирается НОМЕРОМ из списка, который скрипт скачал сам. И тест собирается не по названию видео,
а по его расшифровке — иначе вопросы были бы про то, чего в ролике нет.

Запускается на mini из launchd (scripts/french.sh, deploy/install-french.sh) и со страницы
(POST /api/french/refresh; serve.py сам дёргает его, когда закончились материалы).
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, tempfile, unicodedata, urllib.error, urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "french.json")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import reads as RD  # noqa: E402  — ленты, разбор RSS и превью уже написаны там
import movies as M  # noqa: E402
import books as B  # noqa: E402
import lectures as L  # noqa: E402
import mixes as X  # noqa: E402

LEVELS = ("A1", "A2", "B1", "B2", "C1")
VERDICTS = ("done", "dismissed")
KEEP = 3                  # материалов за подбор

# Виды занятий. Первые два приезжают из лент и с YouTube — настоящий материал, выбранный НОМЕРОМ
# из списка кандидатов. Вторые два модель пишет с нуля, и это не противоречие правилу «ничего не
# выдумывай»: выдумать нельзя ссылку, а объяснение passé composé выдумывать и не надо — оно и есть
# содержание. Тема при этом берётся не с потолка, а из моих ошибок и моего вкуса.
FEED_KINDS = ("video", "article")
DRILL_KINDS = ("lesson", "translation")
# Типы вопросов. Все шесть проверяются на сервере по сохранённому тесту: выбор — по индексу,
# пары — по перестановке, остальное — по тексту (norm).
QUIZ_TYPES = ("choice", "gap", "translate", "order", "match", "listen")
# …кроме открытого перевода: правильных переводов у фразы больше, чем влезает в `accept`, поэтому
# у него судья — человек, и кнопка «всё равно верно» на странице сервером засчитывается.
SELF_TYPES = ("translate",)
DRILL_MIN_Q = 4           # меньше вопросов — это не занятие, а напоминание

# Урок каждый раз другого склада — иначе модель всегда пишет про passé composé. Склад крутится
# по кругу (`drillCursor`), ровно как запросы к YouTube: порядок и есть защита от однообразия.
DRILL_FLAVOURS = [
    {"id": "grammar", "name": "грамматика",
     "brief": "Правило, на котором я спотыкаюсь чаще всего (смотри «Где я ошибаюсь»). Разложи его "
              "на два-три случая, покажи формы, дай примеры на мои темы, а не про Marie et son chat."},
    {"id": "lexis", "name": "лексическое поле",
     "brief": "Слова и выражения вокруг ОДНОЙ темы из моих интересов (кино, история, экономика, "
              "музыка, город, еда). Не выписка из словаря, а то, чем об этом говорят по-французски: "
              "глаголы, сочетания, разница между близкими словами."},
    {"id": "idiom", "name": "живые выражения и связки",
     "brief": "То, что француз вставляет в речь постоянно и чего нет в учебнике: связки (du coup, "
              "en fait, quand même, d'ailleurs), разговорные обороты, смягчения. Обязательно — когда "
              "так говорят, а когда это звучит неуместно."},
    {"id": "verbs", "name": "глагол и его предлоги",
     "brief": "Один-два частых глагола и то, как они управляют: предлоги, конструкции, устойчивые "
              "связки, ложные друзья перевода (assister, attendre, manquer, rendre)."},
]
HISTORY_MAX = 200
ANSWERS_MAX = 400         # журнал ответов: из него растёт память об ошибках
CACHE_MAX = 220
PER_SOURCE = 12
FRESH_DAYS = 21
# Материал на один присест: не лекция и не лонгрид. Верхняя граница важнее нижней — час
# французского «когда-нибудь потом» не смотрится никогда.
VIDEO_MIN_S, VIDEO_MAX_S = 150, 20 * 60
SEARCH_N = 8              # сколько видео берём из одной выдачи
SEARCH_ROTATE = 6         # сколько запросов крутим за раз (иначе каждый раз одни и те же ролики)
TEXT_MAX = 7000           # столько текста уходит во второй проход
TEXT_MIN = 350            # меньше — материала на разбор не хватит
EVERY_HOURS = 20
BOX_DAYS = [0, 1, 3, 7, 16, 35]   # коробка Лейтнера → через сколько дней спросить снова
BOX_MAX = len(BOX_DAYS) - 1
IMG_DIR = os.path.join(ROOT, "french", "img")

# Ленты проверены 15.09.2026: все отдают 200 и настоящий XML. `hard` — подсказка модели о сложности
# языка источника, сам по себе уровень из ленты не виден.
SOURCES = [
    {"id": "1jour1actu", "name": "1jour1actu", "url": "https://www.1jour1actu.com/feed/", "hard": "лёгкий"},
    {"id": "fauth", "name": "Français Authentique", "url": "https://francaisauthentique.com/feed/", "hard": "лёгкий"},
    {"id": "rfi", "name": "RFI", "url": "https://www.rfi.fr/fr/france/rss", "hard": "средний"},
    {"id": "franceinfo", "name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "hard": "средний"},
    {"id": "slate", "name": "Slate.fr", "url": "https://www.slate.fr/rss.xml", "hard": "средний"},
    {"id": "courrier", "name": "Courrier International", "url": "https://www.courrierinternational.com/feed/all/rss.xml", "hard": "средний"},
    {"id": "lemonde", "name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "hard": "средний"},
    {"id": "conversation", "name": "The Conversation", "url": "https://theconversation.com/fr/articles.atom", "hard": "сложный"},
    {"id": "philomag", "name": "Philosophie Magazine", "url": "https://www.philomag.com/rss.xml", "hard": "сложный"},
    {"id": "franceculture", "name": "France Culture", "url": "https://www.radiofrance.fr/franceculture/rss", "hard": "сложный"},
]

# Запросы к YouTube. Половина — каналы для изучающих, половина — настоящие французские каналы
# по темам, которые мне и так интересны (история, философия, наука, экономика, кино):
# язык учится на том, что хочется смотреть и без языка.
SEARCHES = [
    {"q": "innerFrench podcast", "hard": "средний"},
    {"q": "Piece of French podcast français", "hard": "средний"},
    {"q": "Français Authentique vidéo", "hard": "лёгкий"},
    {"q": "Easy French interview rue", "hard": "лёгкий"},
    {"q": "Français avec Pierre grammaire", "hard": "лёгкий"},
    {"q": "Hugo Décrypte actus du jour", "hard": "средний"},
    {"q": "Brut reportage français", "hard": "средний"},
    {"q": "Arte Regards documentaire", "hard": "сложный"},
    {"q": "Blow Up Arte cinéma", "hard": "сложный"},
    {"q": "Nota Bene histoire", "hard": "сложный"},
    {"q": "C'est une autre histoire", "hard": "сложный"},
    {"q": "ScienceEtonnante", "hard": "сложный"},
    {"q": "Heu?reka économie", "hard": "сложный"},
    {"q": "Le Précepteur philosophie", "hard": "сложный"},
    {"q": "Cyrus North philosophie", "hard": "средний"},
    {"q": "Linguisticae langue française", "hard": "средний"},
    {"q": "Fouloscopie", "hard": "средний"},
    {"q": "DirtyBiology", "hard": "сложный"},
    {"q": "France Culture les idées claires", "hard": "сложный"},
    {"q": "Le Monde vidéo décryptage", "hard": "средний"},
    {"q": "actualité en français facile", "hard": "лёгкий"},
    {"q": "documentaire court musique électronique français", "hard": "средний"},
]


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load():
    if not os.path.exists(DATA):
        db = {}
    else:
        with open(DATA, encoding="utf-8") as f:
            db = json.load(f)
    db.setdefault("level", "B1")
    db.setdefault("updatedAt", None)
    db.setdefault("model", None)
    db.setdefault("fetchedAt", None)
    db.setdefault("items", [])        # материалы, лежащие на странице
    db.setdefault("cards", [])        # лексика с интервальным повторением
    db.setdefault("answers", [])      # журнал ответов — из него память об ошибках
    db.setdefault("history", [])      # пройденное и отвергнутое
    db.setdefault("cache", [])        # кандидаты из лент и поиска
    db.setdefault("searchCursor", 0)
    db.setdefault("drillCursor", 0)
    db.setdefault("lastStudy", None)
    db.setdefault("streak", 0)
    return db


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm(s):
    """Сравнение ответов «на глаз»: без регистра, без диакритики, без пунктуации.
    Учу язык, а не раскладку — «est alle» засчитывается как «est allé».
    Кириллица оставлена намеренно: ответы бывают и русскими (перевод на слух, понимание текста),
    а без неё `norm("привет")` давал пустую строку и любые два русских ответа сходились.
    (Та же функция есть в french.html: страница показывает результат сразу, сервер потом
    пересчитывает его сам по сохранённому тесту.)"""
    s = (s or "").replace("’", "'").replace("ʼ", "'")
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\u0430-\u044f' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fmt_min(sec):
    return max(1, round((sec or 0) / 60))


# ---------------------------------------------------------------- кандидаты
def yt_search(query, n=SEARCH_N):
    r = subprocess.run([L.ytdlp(), "--flat-playlist", "-J", f"ytsearch{n}:{query}"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  поиск не отработал ({query}): {r.stderr.strip()[-200:]}", file=sys.stderr)
        return []
    try:
        return json.loads(r.stdout).get("entries") or []
    except json.JSONDecodeError:
        return []


def _articles(cutoff, verbose):
    out = []
    for src in SOURCES:
        try:
            req = urllib.request.Request(src["url"], headers={"User-Agent": RD.UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                items = RD.parse_feed(r.read(), src)
        except (urllib.error.URLError, ET.ParseError, OSError) as e:
            if verbose:
                print(f"  {src['name']}: не прочитал ленту ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        items = [i for i in items if not i["date"] or i["date"] >= cutoff][:PER_SOURCE]
        for i in items:
            i["id"] = "rd:" + i["id"]
            i["kind"] = "article"
            i["hard"] = src["hard"]
        out += items
        if verbose:
            print(f"  {src['name']}: {len(items)}")
    return out


def _videos(db, verbose):
    out = []
    cur = db.get("searchCursor") or 0
    picked = [SEARCHES[(cur + k) % len(SEARCHES)] for k in range(SEARCH_ROTATE)]
    db["searchCursor"] = (cur + SEARCH_ROTATE) % len(SEARCHES)
    for s in picked:
        got = 0
        for e in yt_search(s["q"]):
            if not e.get("id") or e.get("live_status") == "is_live":
                continue
            dur = e.get("duration") or 0
            if not VIDEO_MIN_S <= dur <= VIDEO_MAX_S:
                continue
            out.append({
                "id": "yt:" + e["id"], "kind": "video", "title": (e.get("title") or "").strip(),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                "source": e.get("channel") or e.get("uploader") or "YouTube",
                "sourceId": "youtube", "author": e.get("channel") or e.get("uploader"),
                "date": None, "duration": round(dur), "minutes": fmt_min(dur),
                "hard": s["hard"], "image": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
                "summary": RD.strip_html(e.get("description") or "")[:RD.SUMMARY_CHARS],
                "query": s["q"],
            })
            got += 1
        if verbose:
            print(f"  YouTube «{s['q']}»: {got}")
    return out


def fetch(days=FRESH_DAYS, verbose=True):
    db = load()
    seen = {i["id"] for i in db["items"]} | {h["id"] for h in db["history"]}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    fresh = _articles(cutoff, verbose) + _videos(db, verbose)
    by_id = {c["id"]: c for c in db["cache"]}
    for i in fresh:
        by_id[i["id"]] = i
    cache = [c for c in by_id.values() if c["id"] not in seen]
    # Видео вперёд: их меньше и они дороже достаются, а срезать хвост придётся именно по длине списка.
    cache.sort(key=lambda c: (c["kind"] != "video", c.get("date") or ""), reverse=False)
    videos = [c for c in cache if c["kind"] == "video"][:40]
    arts = sorted([c for c in cache if c["kind"] == "article"],
                  key=lambda c: c.get("date") or "", reverse=True)[:CACHE_MAX - len(videos)]
    db["cache"] = videos + arts
    db["fetchedAt"] = now()
    save(db)
    return db["cache"]


# ---------------------------------------------------------------- тексты материалов
def vtt_text(path):
    """VTT → сплошной текст. Автосубтитры повторяют предыдущую строку в каждом кадре,
    поэтому подряд идущие дубли выбрасываем."""
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if out and out[-1] == line:
            continue
        out.append(line)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def video_text(vid, verbose=True):
    """Расшифровка ролика — французские субтитры (свои или автоматические). Нет французских
    субтитров — нет и материала: по названию теста не соберёшь, а проверить, что ролик правда
    французский, больше нечем."""
    tmp = tempfile.mkdtemp(prefix="fr-subs-")
    try:
        r = subprocess.run([L.ytdlp(), "--skip-download", "--write-subs", "--write-auto-subs",
                            "--sub-langs", "fr.*", "--sub-format", "vtt", "--no-warnings",
                            "-o", os.path.join(tmp, "s.%(ext)s"),
                            f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=180)
        files = sorted(f for f in os.listdir(tmp) if f.endswith(".vtt"))
        if not files:
            if verbose:
                print(f"  нет французских субтитров: {vid} {r.stderr.strip()[-120:]}", file=sys.stderr)
            return None
        return vtt_text(os.path.join(tmp, files[0]))[:TEXT_MAX]
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  субтитры не скачались: {vid} ({type(e).__name__}: {e})", file=sys.stderr)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def article_text(url, verbose=True):
    """Текст статьи — абзацы <p>. Грубо, зато одинаково работает на всех десяти лентах;
    навигация и подписи отсеиваются длиной."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": RD.UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read(1_500_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        if verbose:
            print(f"  страница не открылась: {url} ({type(e).__name__}: {e})", file=sys.stderr)
        return None
    page = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form).*?</\1>", " ", page)
    paras = [RD.strip_html(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)]
    text = " ".join(p for p in paras if len(p) > 60)
    return text[:TEXT_MAX] or None


def material_text(m, verbose=True):
    return video_text(m["id"][3:], verbose) if m["kind"] == "video" else article_text(m["url"], verbose)


def fetch_cover(m):
    """Превью статьи — og:image со страницы, скачанный локально (как в reads.py: внешние CDN
    дома открываются не всегда). У видео превью — ссылка на i.ytimg.com, скачивать нечего."""
    if m.get("kind") != "article":
        return None
    return RD.fetch_image(m, dir_=IMG_DIR, name=m["id"].replace(":", "-"))


def fill_images(db=None):
    """Дозаполняет превью у текущих материалов. Нужно потому, что `french/img/` — данные машины:
    картинку скачал ноутбук, а показывает страницу mini, и файла там нет. Запускать НА MINI,
    это production (ровно как `reads.py images` и `taste_recs.py covers`)."""
    own = db is None
    db = db or load()
    got = []
    for m in db["items"]:
        cur = m.get("image") or ""
        if cur.startswith("http") or (cur and os.path.exists(os.path.join(ROOT, cur))):
            continue
        m.pop("image", None)          # битый путь: пусть fetch_image скачает заново
        if fetch_cover(m):
            got.append(m["title"])
        else:
            m["image"] = m.get("imageUrl") or None   # не скачалось — пусть хоть внешняя ссылка
    if own and got:
        save(db)
    return got


def drop_image(m):
    p = str(m.get("image") or "")
    if p.startswith("french/img/"):
        try:
            os.remove(os.path.join(ROOT, p))
        except OSError:
            pass


# ---------------------------------------------------------------- дайджест (первый проход)
def interests():
    """Вкус — тот же полный, что видят советы по статьям (`reads.taste()`): фильмы с оценками и
    моими заметками, фильмы, которые не зашли, все прочитанные книги, брошенные (сильный сигнал
    против), планы, лекции и музыка. Сначала здесь была выжимка на семь строк, но язык учится на
    том, что интересно и без языка, — значит и материал надо выбирать по всему вкусу, а не по его
    тени: «La Nueve» нашлась именно потому, что в лекциях лежит гражданская война в Испании."""
    return ["## Мой вкус по каталогам сайта — по нему выбирай ТЕМУ материала",
            "(те же данные, по которым мне подбираются статьи; оценки здесь важны не сами по себе,",
            "а как указание, куда смотреть)"] + RD.taste()


def weak_tags(db, limit=12):
    """Темы, где я ошибаюсь. Тег ставит модель на каждый вопрос теста («passé composé»,
    «лексика: работа»), поэтому память об ошибках получается не «6 неверных ответов»,
    а «шесть раз мимо в согласовании прошедшего времени»."""
    agg = {}
    for a in db["answers"]:
        t = (a.get("tag") or "").strip() or "без темы"
        row = agg.setdefault(t, [0, 0])
        row[0] += 1
        if not a.get("correct"):
            row[1] += 1
    rows = [(t, n, w) for t, (n, w) in agg.items() if w]
    rows.sort(key=lambda r: (-r[2], -r[2] / max(1, r[1])))
    return rows[:limit]


def recent_mistakes(db, limit=20):
    return [a for a in db["answers"] if not a.get("correct")][:limit]


def digest():
    db = load()
    out = [f"## Мой уровень: {db['level']}"]
    cards = db["cards"]
    due = [c for c in cards if (c.get("due") or today()) <= today()]
    out.append(f"В колоде {len(cards)} слов, к повторению сегодня {len(due)}. "
               f"Занимался подряд дней: {db.get('streak') or 0}.")
    out += interests()

    w = weak_tags(db)
    if w:
        out.append("\n## Где я ошибаюсь (тема — ошибок из ответов)")
        out += [f"- {t}: {wrong} из {n}" for t, n, wrong in w]
        out.append("Материал, который даёт потренировать именно это, ценнее просто интересного.")
    mis = recent_mistakes(db, 12)
    if mis:
        out.append("\n## Последние ошибки целиком")
        for a in mis:
            out.append(f"- «{a.get('q')}» — я ответил «{a.get('given')}», верно «{a.get('expected')}»")

    if cards:
        known = [c["fr"] for c in cards][:200]
        out.append(f"\n## Слова, которые у меня уже есть в карточках ({len(cards)}) — заново их не объясняй")
        out.append(", ".join(known))

    if db["items"]:
        out.append("\n## Материалы, которые сейчас лежат у меня на странице (не повторяй)")
        out += [f"- {i['title']}" for i in db["items"]]
    if db["history"]:
        out.append("\n## Что было раньше (свежее сверху)")
        word = {"done": "ПРОШЁЛ", "dismissed": "МИМО", "new": "без ответа"}
        for h in db["history"][:30]:
            line = f"- {word.get(h.get('verdict'), h.get('verdict'))}: {h.get('title')} ({h.get('source')})"
            if h.get("score") is not None:
                line += f" — тест {h['score']} из {h.get('total')}"
            if h.get("reason"):
                line += f" — советовал: {h['reason']}"
            out.append(line)
    else:
        out.append("\n## Что было раньше\n(это первый подбор)")

    out.append("\n## Кандидаты. Выбирай ТОЛЬКО из них, по номеру.")
    for n, c in enumerate(db["cache"], 1):
        kind = "видео" if c["kind"] == "video" else "статья"
        bits = [kind, c.get("source") or "", f"{c['minutes']} мин" if c.get("minutes") else "", c.get("hard") or ""]
        out.append(f"{n}. [{' · '.join(b for b in bits if b)}] {c['title']}")
        if c.get("summary"):
            out.append(f"   {c['summary']}")
    return "\n".join(out)


# ---------------------------------------------------------------- выбор модели → материалы
def parse_answer(text, key="n"):
    text = re.sub(r"```[a-z]*", "", text)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < i:
        raise SystemExit("no JSON array in the model answer")
    data = json.loads(text[i:j + 1])
    if not isinstance(data, list):
        raise SystemExit("model answer is not a list")
    return [c for c in data if isinstance(c, dict) and c.get(key) is not None]


def apply_answer(text, model=None, keep=KEEP):
    """Номера из ответа модели → материалы с настоящим текстом. Материал без текста
    (у ролика нет французских субтитров, статья не открылась) пропускается: разбор по нему
    собрать нечем, а «французское» видео без французских субтитров обычно и не французское."""
    db = load()
    cache = db["cache"]
    items = []
    for c in parse_answer(text):
        if len(items) >= keep:
            break
        try:
            n = int(c["n"])
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(cache):
            print(f"  номер вне списка: {c['n']}")
            continue
        m = dict(cache[n - 1])
        if any(i["id"] == m["id"] for i in items):
            continue
        body = material_text(m)
        if not body or len(body) < TEXT_MIN:
            print(f"  без текста, пропускаю: {m['title']}")
            continue
        m.update(text=body, why=(c.get("why") or "").strip() or None, level=db["level"],
                 addedAt=today(), verdict="new", lesson=None)
        if m["kind"] == "article":
            m["imageUrl"] = m.get("image")
            fetch_cover(m)
        items.append(m)
        print(f"  + [{'видео' if m['kind'] == 'video' else 'статья'}] {m['title']} ({len(body)} зн.)")
    if not items:
        raise SystemExit("ни одного материала не подтвердилось")
    # Упражнения (урок, перевод) живут своим проходом и этим подбором не трогаются.
    drills = [r for r in db["items"] if r.get("kind") in DRILL_KINDS]
    stale = [r for r in db["items"]
             if r.get("kind") not in DRILL_KINDS and r["id"] not in {i["id"] for i in items}]
    for r in stale:
        drop_image(r)
    db["history"] = [history_entry(r) for r in stale] + db["history"]
    db["history"] = db["history"][:HISTORY_MAX]
    db["items"] = items + drills
    chosen = {i["id"] for i in items}
    db["cache"] = [c for c in db["cache"] if c["id"] not in chosen]
    db["updatedAt"] = now()
    db["model"] = model or db.get("model")
    save(db)
    return items


# ---------------------------------------------------------------- разбор (второй проход)
def lesson_digest():
    db = load()
    need = [i for i in db["items"] if not i.get("lesson") and i.get("text")]
    if not need:
        raise SystemExit("все материалы уже с разбором")
    out = [f"## Мой уровень: {db['level']}"]
    w = weak_tags(db)
    if w:
        out.append("## Где я ошибаюсь — сюда и целься вопросами")
        out += [f"- {t}: {wrong} ошибок из {n}" for t, n, wrong in w]
    if db["cards"]:
        out.append(f"\n## Слова, которые у меня уже есть ({len(db['cards'])}) — в новые не бери")
        out.append(", ".join(c["fr"] for c in db["cards"][:200]))
    for n, m in enumerate(need, 1):
        kind = "видео" if m["kind"] == "video" else "статья"
        out.append(f"\n### Материал {n} — {kind} «{m['title']}» ({m.get('source')}, {m.get('minutes')} мин)")
        if m.get("why"):
            out.append(f"Зачем он мне: {m['why']}")
        out.append("Текст (расшифровка видео или текст статьи):")
        out.append('"""')
        out.append(m["text"])
        out.append('"""')
    return "\n".join(out)


def _clean_words(raw, limit=10):
    words = []
    for w in (raw or [])[:limit]:
        if isinstance(w, dict) and (w.get("fr") or "").strip() and (w.get("ru") or "").strip():
            words.append({"fr": w["fr"].strip(), "ru": w["ru"].strip(),
                          "ex": (w.get("ex") or "").strip() or None})
    return words


def _clean_q(q):
    """Один вопрос теста. Шесть типов, и каждый проверяется сервером по сохранённому тесту:
    выбор — по индексу, пары — по перестановке, остальное — по тексту. Кривой вопрос
    выбрасывается молча: один битый вопрос не стоит того, чтобы терять материал целиком."""
    if not isinstance(q, dict):
        return None
    t = (q.get("type") or "").strip().lower()
    if t not in QUIZ_TYPES:
        t = "choice" if q.get("options") else ("match" if q.get("pairs") else "gap")
    out = {"type": t, "q": (q.get("q") or "").strip(),
           "explain": (q.get("explain") or "").strip() or None,
           "hint": (q.get("hint") or "").strip() or None,
           "tag": (q.get("tag") or "").strip() or None}
    if t == "choice":
        opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError):
            return None
        if len(opts) < 3 or not 0 <= ans < len(opts):
            return None
        out.update(options=opts, answer=ans)
    elif t == "match":
        pairs = [{"fr": str(p.get("fr")).strip(), "ru": str(p.get("ru")).strip()}
                 for p in (q.get("pairs") or [])
                 if isinstance(p, dict) and str(p.get("fr") or "").strip() and str(p.get("ru") or "").strip()]
        if len(pairs) < 3:
            return None
        out.update(pairs=pairs[:6], q=out["q"] or "Составь пары")
    else:
        ans = str(q.get("answer") or "").strip()
        if not ans:
            return None
        if t == "order" and len(ans.split()) < 3:
            return None      # «собери фразу» из двух слов собирается сама
        if t == "listen":
            say = (q.get("say") or "").strip()
            if not say:
                return None
            out.update(say=say, q=out["q"] or "Что ты услышал? Впиши по-французски")
        out.update(answer=ans, accept=[str(a).strip() for a in (q.get("accept") or []) if str(a).strip()])
    if not out["q"]:
        return None
    return out


def _clean_quiz(raw, limit=10):
    return [x for x in (_clean_q(q) for q in (raw or [])[:limit]) if x]


def _clean_sections(raw, limit=5):
    """Объяснение урока: две-четыре части, у каждой примеры парами fr/ru."""
    out = []
    for sec in (raw or [])[:limit]:
        if not isinstance(sec, dict):
            continue
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        ex = [{"fr": str(e.get("fr")).strip(), "ru": (str(e.get("ru")).strip() or None)}
              for e in (sec.get("ex") or [])[:6]
              if isinstance(e, dict) and str(e.get("fr") or "").strip()]
        out.append({"h": (sec.get("h") or "").strip() or None, "text": text, "ex": ex})
    return out


def _clean_lesson(raw, item=None):
    """Разбор от модели → то, что можно показать."""
    words = _clean_words(raw.get("words"))
    quiz = _clean_quiz(raw.get("quiz"), 8)
    if not words and not quiz:
        return None
    return {"intro": (raw.get("intro") or "").strip() or None, "words": words, "quiz": quiz,
            "builtAt": now()}


def apply_lessons(text, model=None):
    db = load()
    need = [i for i in db["items"] if not i.get("lesson") and i.get("text")]
    got = 0
    for c in parse_answer(text):
        try:
            n = int(c["n"])
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(need):
            print(f"  номер материала вне списка: {c['n']}")
            continue
        m = need[n - 1]
        lesson = _clean_lesson(c, m)
        if not lesson:
            print(f"  пустой разбор: {m['title']}")
            continue
        m["lesson"] = lesson
        m.pop("text", None)   # текст нужен был только для этого прохода
        got += 1
        print(f"  + разбор «{m['title']}»: {len(lesson['words'])} слов, {len(lesson['quiz'])} вопросов")
    if not got:
        raise SystemExit("ни одного разбора не собралось")
    db["model"] = model or db.get("model")
    db["updatedAt"] = now()
    save(db)
    return got


# ---------------------------------------------------------------- упражнения (третий проход)
# Видео и статья — это чужая речь, которую я понимаю или не понимаю. Урок и перевод — другое:
# тут я сам достаю французский из головы. Поэтому они и пишутся с нуля, а не ищутся в лентах.
def _drill_flavour(db):
    return DRILL_FLAVOURS[(db.get("drillCursor") or 0) % len(DRILL_FLAVOURS)]


def drill_digest():
    """Что видит модель, когда пишет урок и перевод. Тема урока — не на её усмотрение: склад
    задан очередью (`drillCursor`), содержание — моими ошибками, примеры — моим вкусом.
    Перевод собирается из слов, которые уже лежат в колоде: это повторение в контексте,
    а не ещё одна порция незнакомого."""
    db = load()
    fl = _drill_flavour(db)
    out = [f"## Мой уровень: {db['level']}",
           f"## Склад сегодняшнего урока: {fl['name']}", fl["brief"]]

    w = weak_tags(db)
    if w:
        out.append("\n## Где я ошибаюсь (тема — ошибок из ответов). Урок бей сюда.")
        out += [f"- {t}: {wrong} из {n}" for t, n, wrong in w]
    else:
        out.append("\n## Где я ошибаюсь\n(журнал пока пуст — бери то, на чём обычно спотыкаются "
                   f"на уровне {db['level']})")
    mis = recent_mistakes(db, 10)
    if mis:
        out.append("\n## Последние ошибки целиком")
        for a in mis:
            out.append(f"- «{a.get('q')}» — я ответил «{a.get('given')}», верно «{a.get('expected')}»")

    if db["cards"]:
        due_now = due_cards(db)
        out.append(f"\n## Слова, которые уже лежат у меня в колоде ({len(db['cards'])})")
        out.append(", ".join(f"{c['fr']} — {c['ru']}" for c in db["cards"][-80:]))
        if due_now:
            out.append(f"Из них к повторению сегодня: {', '.join(c['fr'] for c in due_now[:30])}.")
        out.append("В переводе опирайся на них: пусть фразы требуют именно этих слов — "
                   "так повторение идёт в контексте, а не карточками.")
    else:
        out.append("\n## Колода пуста — это первое занятие, слов ещё нет.")

    out += interests()

    prev = [h for h in db["history"] if h.get("kind") in DRILL_KINDS][:12]
    if prev:
        out.append("\n## Уроки и переводы, которые уже были (тему не повторяй)")
        word = {"done": "ПРОШЁЛ", "dismissed": "МИМО", "new": "без ответа"}
        for h in prev:
            line = f"- {word.get(h.get('verdict'), h.get('verdict'))}: {h.get('title')}"
            if h.get("topic"):
                line += f" [{h['topic']}]"
            if h.get("score") is not None:
                line += f" — {h['score']} из {h.get('total')}"
            out.append(line)

    now_items = [i for i in db["items"] if i.get("kind") in FEED_KINDS]
    if now_items:
        out.append("\n## Что сейчас лежит у меня на странице рядом (видео и статьи этого подбора)")
        out += [f"- {i['title']}" + (f" — {i['why']}" if i.get("why") else "") for i in now_items]
        out.append("Урок и перевод должны их дополнять, а не пересказывать.")
    return "\n".join(out)


def _drill_id(kind, title):
    tag = "ls" if kind == "lesson" else "tr"
    return f"{tag}:{hashlib.md5((kind + title + today()).encode('utf-8')).hexdigest()[:10]}"


def _clean_drill(raw, db):
    kind = (raw.get("kind") or "").strip().lower()
    if kind not in DRILL_KINDS:
        return None
    title = (raw.get("title") or "").strip()
    quiz = _clean_quiz(raw.get("quiz"), 10)
    words = _clean_words(raw.get("words"))
    sections = _clean_sections(raw.get("sections")) if kind == "lesson" else []
    if not title or len(quiz) < DRILL_MIN_Q:
        return None
    if kind == "lesson" and (len(sections) < 2 or not words):
        return None          # урок без объяснения — это просто тест
    if kind == "translation" and not any(q["type"] in ("translate", "order") for q in quiz):
        return None          # перевод, в котором нечего переводить
    return {"id": _drill_id(kind, title), "kind": kind, "title": title,
            "topic": (raw.get("topic") or "").strip() or None,
            "source": "Урок" if kind == "lesson" else "Перевод",
            "flavour": _drill_flavour(db)["id"] if kind == "lesson" else None,
            "why": (raw.get("why") or "").strip() or None,
            "minutes": max(4, round((len(quiz) * 1.2 + len(words) * 0.4 + len(sections)))),
            "level": db["level"], "addedAt": today(), "verdict": "new",
            "lesson": {"intro": (raw.get("intro") or "").strip() or None, "sections": sections,
                       "words": words, "quiz": quiz, "builtAt": now()}}


def apply_drills(text, model=None):
    """Ответ модели → урок и перевод на странице. Старые упражнения уезжают в историю целиком:
    их не ищут, а пишут заново каждый подбор, и вчерашний урок незакрытым висеть не должен."""
    db = load()
    got = []
    for raw in parse_answer(text, key="kind"):
        d = _clean_drill(raw, db)
        if not d:
            print(f"  упражнение не приняли: {(raw.get('title') or raw.get('kind') or '?')!r}")
            continue
        if any(x["kind"] == d["kind"] for x in got):
            continue
        got.append(d)
        print(f"  + [{d['source'].lower()}] {d['title']} — {len(d['lesson']['words'])} слов, "
              f"{len(d['lesson']['quiz'])} заданий")
    if not got:
        raise SystemExit("ни одного упражнения не собралось")
    stale = [r for r in db["items"] if r.get("kind") in DRILL_KINDS]
    db["history"] = ([history_entry(r) for r in stale] + db["history"])[:HISTORY_MAX]
    db["items"] = [r for r in db["items"] if r.get("kind") not in DRILL_KINDS] + got
    db["drillCursor"] = (db.get("drillCursor") or 0) + 1
    db["updatedAt"] = now()
    db["model"] = model or db.get("model")
    save(db)
    return got


# ---------------------------------------------------------------- занятие
def history_entry(m, score=None, total=None):
    return {"id": m["id"], "title": m.get("title"), "url": m.get("url"), "source": m.get("source"),
            "kind": m.get("kind"), "topic": m.get("topic"), "reason": m.get("why"),
            "verdict": m.get("verdict") or "new", "score": score, "total": total,
            "at": m.get("decidedAt") or m.get("addedAt") or today()}


def _touch(db):
    """Стрик: занимался ли я вчера. Нужен ровно для одной строки на странице, но без него
    непонятно, живёт ли эта затея вообще."""
    last = db.get("lastStudy")
    d = today()
    if last == d:
        return
    y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    db["streak"] = (db.get("streak") or 0) + 1 if last == y else 1
    db["lastStudy"] = d


def _log(db, entries):
    db["answers"] = entries + db["answers"]
    db["answers"] = db["answers"][:ANSWERS_MAX]


def _grade(q, given, self_ok=False):
    """→ (верно, что я ответил, как правильно). Выбор проверяется по индексу, пары — по
    перестановке, всё остальное (пропуск, перевод, порядок слов, на слух) — по тексту.
    У открытого перевода судья я: переводов у фразы больше, чем влезает в `accept`, поэтому
    `self` в ответе засчитывает вариант, которого сервер не знал. Только у перевода — в
    остальных типах ответ один, и верить клиенту не за что."""
    t = q.get("type") or "gap"
    if t == "choice":
        opts = q.get("options") or []
        right = opts[q["answer"]] if 0 <= q.get("answer", -1) < len(opts) else ""
        try:
            i = int(given)
        except (TypeError, ValueError):
            return False, str(given), right
        return i == int(q["answer"]), (opts[i] if 0 <= i < len(opts) else str(given)), right
    if t == "match":
        pairs = q.get("pairs") or []
        right = " · ".join(f"{p['fr']} — {p['ru']}" for p in pairs)
        try:
            order = [int(x) for x in str(given or "").split(",") if x.strip() != ""]
        except ValueError:
            return False, str(given or ""), right
        shown = " · ".join(f"{pairs[n]['fr']} — {pairs[i]['ru']}" if 0 <= i < len(pairs) else "—"
                           for n, i in enumerate(order) if n < len(pairs)) or "—"
        return order == list(range(len(pairs))), shown, right
    ok = norm(given) in {norm(q.get("answer"))} | {norm(a) for a in (q.get("accept") or [])}
    if not ok and self_ok and t in SELF_TYPES and str(given or "").strip():
        ok = True
    return ok, str(given or ""), q.get("answer") or ""


def finish(mid, answers=None, verdict="done"):
    """Материал пройден: ответы в журнал (сервер пересчитывает их сам по сохранённому тесту),
    слова — в колоду, материал — в историю."""
    if verdict not in VERDICTS:
        raise SystemExit(f"bad verdict: {verdict} ({'|'.join(VERDICTS)})")
    db = load()
    m = next((x for x in db["items"] if x["id"] == mid), None)
    if not m:
        raise SystemExit(f"нет такого материала: {mid}")
    m["verdict"] = verdict
    m["decidedAt"] = today()
    score = total = None
    added = 0
    if verdict == "done":
        quiz = ((m.get("lesson") or {}).get("quiz")) or []
        log = []
        score = 0
        asked = set(m.get("asked") or [])   # отвеченное с полки уже в журнале — второй раз не считаем
        for a in (answers or []):
            try:
                i = int(a.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(quiz) or i in asked:
                continue
            q = quiz[i]
            ok, given, expected = _grade(q, a.get("given"), bool(a.get("self")))
            score += 1 if ok else 0
            log.append({"at": now(), "material": m["id"], "title": m.get("title"), "kind": "quiz",
                        "type": q.get("type"), "tag": q.get("tag"), "q": q["q"], "given": given,
                        "expected": expected, "correct": ok})
        total = len([a for a in log])
        _log(db, log)
        added = _add_cards(db, m)
        _touch(db)
    else:
        drop_image(m)
    db["items"] = [x for x in db["items"] if x["id"] != mid]
    db["history"] = ([history_entry(m, score, total)] + db["history"])[:HISTORY_MAX]
    save(db)
    # `left` — только материалы из лент: упражнения пишутся своим проходом и кончаются отдельно,
    # поэтому serve.py по этим двум числам решает, за чем идти — за новыми видео или за новым уроком.
    return {"id": mid, "verdict": verdict, "score": score, "total": total, "cards": added,
            "left": len([x for x in db["items"] if x.get("kind") in FEED_KINDS]),
            "drills": len([x for x in db["items"] if x.get("kind") in DRILL_KINDS])}


SHELF_TYPES = ("choice", "gap")   # что влезает в одну строку на полке главной: выбор и пропуск


def one_question():
    """Один вопрос на полку главной (20.09.2026). Страница французского с четырьмя материалами и
    колодой не открывалась ни разу за неделю (`lastStudy` пустой, ответов 0): занятие на полчаса
    не начинается само. Вопрос на десять секунд — начинается. Берётся первый не заданный вопрос
    подходящего типа из текущих материалов; ответ уходит в тот же журнал ошибок, что и тест."""
    db = load()
    for m in db["items"]:
        quiz = ((m.get("lesson") or {}).get("quiz")) or []
        asked = set(m.get("asked") or [])
        for i, q in enumerate(quiz):
            if i in asked or (q.get("type") or "gap") not in SHELF_TYPES:
                continue
            return {"id": m["id"], "i": i, "title": m.get("title"), "kind": m.get("kind"),
                    "type": q.get("type") or "gap", "q": q.get("q"), "hint": q.get("hint"),
                    "options": q.get("options") if q.get("type") == "choice" else None,
                    "tag": q.get("tag"),
                    "left": sum(1 for x in db["items"] for k, y in enumerate(((x.get("lesson") or {}).get("quiz")) or [])
                                if k not in set(x.get("asked") or []) and (y.get("type") or "gap") in SHELF_TYPES) - 1}
    return None


def answer_one(mid, i, given):
    """Ответ на вопрос с полки: проверяется сервером по сохранённому тесту, как и в `finish`."""
    db = load()
    m = next((x for x in db["items"] if x["id"] == mid), None)
    if not m:
        raise SystemExit(f"нет такого материала: {mid}")
    quiz = ((m.get("lesson") or {}).get("quiz")) or []
    try:
        i = int(i)
    except (TypeError, ValueError):
        raise SystemExit("bad i")
    if not 0 <= i < len(quiz):
        raise SystemExit("bad i")
    q = quiz[i]
    ok, shown, expected = _grade(q, given)
    _log(db, [{"at": now(), "material": m["id"], "title": m.get("title"), "kind": "shelf",
               "type": q.get("type"), "tag": q.get("tag"), "q": q["q"], "given": shown,
               "expected": expected, "correct": ok}])
    asked = set(m.get("asked") or [])
    asked.add(i)
    m["asked"] = sorted(asked)
    _touch(db)
    save(db)
    return {"correct": ok, "given": shown, "expected": expected, "explain": q.get("explain")}


def _add_cards(db, m):
    have = {norm(c["fr"]) for c in db["cards"]}
    added = 0
    for w in ((m.get("lesson") or {}).get("words") or []):
        if norm(w["fr"]) in have:
            continue
        have.add(norm(w["fr"]))
        db["cards"].append({"id": f"{m['id']}:{len(db['cards'])}", "fr": w["fr"], "ru": w["ru"],
                            "ex": w.get("ex"), "from": m["id"], "fromTitle": m.get("title"),
                            "addedAt": today(), "box": 1, "due": today(), "seen": 0, "wrong": 0,
                            "lastAt": None})
        added += 1
    return added


def due_cards(db, limit=None):
    d = today()
    cards = [c for c in db["cards"] if (c.get("due") or d) <= d]
    cards.sort(key=lambda c: (c.get("box") or 1, c.get("due") or d))
    return cards[:limit] if limit else cards


def review(results):
    """Итог сессии карточек: коробка вверх при верном ответе, обратно в первую — при ошибке."""
    db = load()
    by_id = {c["id"]: c for c in db["cards"]}
    log, ok_n = [], 0
    for r in (results or []):
        c = by_id.get(r.get("id"))
        if not c:
            continue
        ok = bool(r.get("correct"))
        ok_n += 1 if ok else 0
        c["seen"] = (c.get("seen") or 0) + 1
        c["lastAt"] = now()
        if ok:
            c["box"] = min(BOX_MAX, (c.get("box") or 1) + 1)
        else:
            c["wrong"] = (c.get("wrong") or 0) + 1
            c["box"] = 1
        c["due"] = (datetime.date.today() + datetime.timedelta(days=BOX_DAYS[c["box"]])).isoformat()
        log.append({"at": now(), "material": c.get("from"), "title": c.get("fromTitle"),
                    "kind": "card", "type": r.get("mode") or "recognize", "tag": "лексика",
                    "q": c["fr"], "given": r.get("given") or "", "expected": c["ru"], "correct": ok})
    if log:
        _log(db, log)
        _touch(db)
        save(db)
    return {"seen": len(log), "correct": ok_n, "due": len(due_cards(db))}


def set_level(level):
    level = level.upper()
    if level not in LEVELS:
        raise SystemExit(f"bad level: {level} ({'|'.join(LEVELS)})")
    db = load()
    db["level"] = level
    save(db)
    return level


def accuracy(db, n=40):
    last = db["answers"][:n]
    return round(100 * sum(1 for a in last if a.get("correct")) / len(last)) if last else None


def summary():
    """То, что видит страница: материалы с разборами, колода, ошибки и статистика.
    Кэш кандидатов и тексты материалов остаются на сервере — странице они не нужны."""
    db = load()
    items = []
    for m in db["items"]:
        m = {k: v for k, v in m.items() if k != "text"}
        items.append(m)
    cards = [{k: c.get(k) for k in ("id", "fr", "ru", "ex", "box", "due", "seen", "wrong", "fromTitle")}
             for c in db["cards"]]
    return {"level": db["level"], "levels": list(LEVELS), "updatedAt": db["updatedAt"],
            "model": db.get("model"), "items": items, "cards": cards,
            "due": [c["id"] for c in due_cards(db)],
            "history": db["history"][:24],
            "weak": [{"tag": t, "n": n, "wrong": w} for t, n, w in weak_tags(db, 8)],
            "mistakes": recent_mistakes(db, 8),
            "streak": db.get("streak") or 0, "accuracy": accuracy(db),
            "stats": {"cards": len(db["cards"]), "due": len(due_cards(db)),
                      "answers": len(db["answers"]),
                      "drills": sum(1 for i in db["items"] if i.get("kind") in DRILL_KINDS),
                      "done": sum(1 for h in db["history"] if h.get("verdict") == "done")}}


def due():
    """Пора ли искать новое: материалы кончились или прошли сутки."""
    db = load()
    feed = [i for i in db["items"] if i.get("kind") in FEED_KINDS]
    fresh = [i for i in feed if i.get("lesson")]
    if not feed:
        return True, "материалов нет"
    if not fresh:
        return True, "материалы есть, но без разборов"
    if not db.get("updatedAt"):
        return True, "ни разу не запускался"
    try:
        last = datetime.datetime.fromisoformat(db["updatedAt"])
    except ValueError:
        return True, "битый updatedAt"
    hours = (datetime.datetime.now() - last).total_seconds() / 3600
    if hours >= EVERY_HOURS:
        return True, f"прошло {hours:.1f} ч"
    return False, f"прошло {hours:.1f} ч, следующий через {EVERY_HOURS - hours:.1f} ч"


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--days", type=int, default=FRESH_DAYS)
    sub.add_parser("digest")
    sub.add_parser("lesson-digest")
    sub.add_parser("drill-digest")
    sub.add_parser("due")
    sub.add_parser("list")
    sub.add_parser("cards")
    sub.add_parser("stats")
    sub.add_parser("images", help="дозаполнить превью материалов (на mini — это production)")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=KEEP)
    ls = sub.add_parser("lessons")
    ls.add_argument("--file", required=True)
    ls.add_argument("--model")
    dr = sub.add_parser("drills")
    dr.add_argument("--file", required=True)
    dr.add_argument("--model")
    fi = sub.add_parser("finish")
    fi.add_argument("id")
    fi.add_argument("--verdict", choices=VERDICTS, default="done")
    lv = sub.add_parser("level")
    lv.add_argument("level", choices=[l.lower() for l in LEVELS] + list(LEVELS))
    args = ap.parse_args()

    if args.cmd == "fetch":
        cache = fetch(args.days)
        print(f"кандидатов: {len(cache)} ({sum(1 for c in cache if c['kind'] == 'video')} видео)")
    elif args.cmd == "images":
        got = fill_images()
        print("\n".join(got) if got else "нечего дозаполнять")
    elif args.cmd == "digest":
        print(digest())
    elif args.cmd == "lesson-digest":
        print(lesson_digest())
    elif args.cmd == "drill-digest":
        print(drill_digest())
    elif args.cmd == "apply":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        items = apply_answer(text, args.model, args.keep)
        print(f"{len(items)} материалов сохранено в french.json")
    elif args.cmd == "lessons":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        print(f"{apply_lessons(text, args.model)} разборов собрано")
    elif args.cmd == "drills":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        print(f"{len(apply_drills(text, args.model))} упражнений собрано")
    elif args.cmd == "finish":
        out = finish(args.id, [], args.verdict)
        print(f"{args.id}: {out['verdict']}, слов в колоду {out['cards']}")
    elif args.cmd == "level":
        print(f"уровень: {set_level(args.level)}")
    elif args.cmd == "due":
        ok, why = due()
        print(why)
        sys.exit(0 if ok else 1)
    elif args.cmd == "cards":
        db = load()
        d = due_cards(db)
        print(f"слов {len(db['cards'])}, к повторению {len(d)}")
        for c in d[:30]:
            print(f"  [{c['box']}] {c['fr']} — {c['ru']}" + (f"  (ошибок {c['wrong']})" if c.get("wrong") else ""))
    elif args.cmd == "stats":
        s = summary()
        print(f"уровень {s['level']}, слов {s['stats']['cards']}, к повторению {s['stats']['due']}, "
              f"пройдено материалов {s['stats']['done']}, точность {s['accuracy']}%, стрик {s['streak']}")
        for w in s["weak"]:
            print(f"  слабое место: {w['tag']} — {w['wrong']} из {w['n']}")
    elif args.cmd == "list":
        db = load()
        print(f"обновлено {db.get('updatedAt')} ({db.get('model')}), уровень {db['level']}, "
              f"кандидатов {len(db['cache'])}")
        for m in db["items"]:
            les = m.get("lesson") or {}
            print(f"  {m['id']}  [{m['kind']}] {m['title']} — {m.get('source')}, {m.get('minutes')} мин")
            if m.get("why"):
                print(f"      {m['why']}")
            if les:
                kinds = ", ".join(sorted({q["type"] for q in (les.get("quiz") or [])}))
                print(f"      разбор: {len(les.get('words') or [])} слов, "
                      f"{len(les.get('quiz') or [])} заданий ({kinds})")
            else:
                print("      разбора ещё нет")


if __name__ == "__main__":
    main()
