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
import argparse, datetime, json, os, re, shutil, subprocess, sys, tempfile, unicodedata, urllib.error, urllib.request
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
    (Та же функция есть в french.html: страница показывает результат сразу, сервер потом
    пересчитывает его сам по сохранённому тесту.)"""
    s = (s or "").replace("’", "'").replace("ʼ", "'")
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
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


def drop_image(m):
    p = str(m.get("image") or "")
    if p.startswith("french/img/"):
        try:
            os.remove(os.path.join(ROOT, p))
        except OSError:
            pass


# ---------------------------------------------------------------- дайджест (первый проход)
def interests():
    """Интересы — коротко, по каталогам сайта. Полный вкус, как у статей, здесь не нужен:
    на французском важна тема, а не то, какую оценку я поставил «Сталкеру»."""
    out = ["## Что мне интересно (каталоги сайта)"]
    movies = M.load()
    rated = sorted([m for m in movies if m.get("rating")], key=lambda m: -m["rating"])[:12]
    if rated:
        out.append("- Любимые фильмы: " + "; ".join(
            m["title"] + (f" — {m['director']}" if m.get("director") else "") for m in rated))
    genres = {}
    for m in movies:
        for g in (m.get("genre") or []):
            genres[g] = genres.get(g, 0) + 1
    if genres:
        out.append("- Жанры кино: " + ", ".join(g for g, _ in sorted(genres.items(), key=lambda kv: -kv[1])[:7]))
    books = B.load()
    read = [b for b in books if b.get("status") == "read"][-14:]
    if read:
        out.append("- Из прочитанных книг: " + "; ".join(f"{b['title']} ({b.get('author') or '?'})" for b in read))
    lec = L.load()
    series = sorted({(lec["series"].get(x) or "").rstrip(".") for l in lec["lectures"]
                     for x in (l.get("series") or []) if lec["series"].get(x)})
    listened = [l["title"] for l in lec["lectures"] if l.get("status") in ("listened", "listening")][-10:]
    if series:
        out.append("- Темы лекций, которые я слушаю: " + ", ".join(s for s in series if s)[:400])
    if listened:
        out.append("- Последнее из лекций: " + "; ".join(listened))
    mixes = X.load()
    tags = {}
    for m in mixes:
        if m.get("genre"):
            tags[m["genre"]] = tags.get(m["genre"], 0) + 1
    if tags:
        out.append("- Музыка: " + ", ".join(g for g, _ in sorted(tags.items(), key=lambda kv: -kv[1])[:8]))
    return out


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
            # Двоеточию из id («rd:abc») в имени файла делать нечего: путь уезжает и в URL, и в rsync.
            RD.fetch_image(m, dir_=IMG_DIR, name=m["id"].replace(":", "-"))
        items.append(m)
        print(f"  + [{'видео' if m['kind'] == 'video' else 'статья'}] {m['title']} ({len(body)} зн.)")
    if not items:
        raise SystemExit("ни одного материала не подтвердилось")
    stale = [r for r in db["items"] if r["id"] not in {i["id"] for i in items}]
    for r in stale:
        drop_image(r)
    db["history"] = [history_entry(r) for r in stale] + db["history"]
    db["history"] = db["history"][:HISTORY_MAX]
    db["items"] = items
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


def _clean_lesson(raw, item):
    """Разбор от модели → то, что можно показать. Кривые вопросы выбрасываем молча:
    один битый вопрос не стоит того, чтобы терять материал целиком."""
    words = []
    for w in (raw.get("words") or [])[:10]:
        if isinstance(w, dict) and (w.get("fr") or "").strip() and (w.get("ru") or "").strip():
            words.append({"fr": w["fr"].strip(), "ru": w["ru"].strip(),
                          "ex": (w.get("ex") or "").strip() or None})
    quiz = []
    for q in (raw.get("quiz") or [])[:8]:
        if not isinstance(q, dict) or not (q.get("q") or "").strip():
            continue
        t = q.get("type") or ("choice" if q.get("options") else "gap")
        item_q = {"type": t, "q": q["q"].strip(), "explain": (q.get("explain") or "").strip() or None,
                  "tag": (q.get("tag") or "").strip() or None}
        if t == "choice":
            opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
            try:
                ans = int(q.get("answer"))
            except (TypeError, ValueError):
                continue
            if len(opts) < 3 or not 0 <= ans < len(opts):
                continue
            item_q.update(options=opts, answer=ans)
        else:
            ans = (str(q.get("answer") or "")).strip()
            if not ans:
                continue
            item_q.update(type="gap", answer=ans,
                          accept=[str(a).strip() for a in (q.get("accept") or []) if str(a).strip()])
        quiz.append(item_q)
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


# ---------------------------------------------------------------- занятие
def history_entry(m, score=None, total=None):
    return {"id": m["id"], "title": m.get("title"), "url": m.get("url"), "source": m.get("source"),
            "kind": m.get("kind"), "reason": m.get("why"), "verdict": m.get("verdict") or "new",
            "score": score, "total": total, "at": m.get("decidedAt") or m.get("addedAt") or today()}


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


def _grade(q, given):
    if q["type"] == "choice":
        try:
            return int(given) == int(q["answer"]), (q["options"][int(given)] if 0 <= int(given) < len(q["options"]) else str(given)), q["options"][q["answer"]]
        except (TypeError, ValueError, IndexError):
            return False, str(given), q["options"][q["answer"]]
    ok = norm(given) in {norm(q["answer"])} | {norm(a) for a in (q.get("accept") or [])}
    return ok, str(given or ""), q["answer"]


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
        for a in (answers or []):
            try:
                i = int(a.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(quiz):
                continue
            q = quiz[i]
            ok, given, expected = _grade(q, a.get("given"))
            score += 1 if ok else 0
            log.append({"at": now(), "material": m["id"], "title": m.get("title"), "kind": "quiz",
                        "tag": q.get("tag"), "q": q["q"], "given": given, "expected": expected,
                        "correct": ok})
        total = len([a for a in log])
        _log(db, log)
        added = _add_cards(db, m)
        _touch(db)
    else:
        drop_image(m)
    db["items"] = [x for x in db["items"] if x["id"] != mid]
    db["history"] = ([history_entry(m, score, total)] + db["history"])[:HISTORY_MAX]
    save(db)
    return {"id": mid, "verdict": verdict, "score": score, "total": total, "cards": added,
            "left": len(db["items"])}


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
                    "kind": "card", "tag": "лексика", "q": c["fr"], "given": r.get("given") or "",
                    "expected": c["ru"], "correct": ok})
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
                      "done": sum(1 for h in db["history"] if h.get("verdict") == "done")}}


def due():
    """Пора ли искать новое: материалы кончились или прошли сутки."""
    db = load()
    fresh = [i for i in db["items"] if i.get("lesson")]
    if not db["items"]:
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
    sub.add_parser("due")
    sub.add_parser("list")
    sub.add_parser("cards")
    sub.add_parser("stats")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--model")
    a.add_argument("--keep", type=int, default=KEEP)
    ls = sub.add_parser("lessons")
    ls.add_argument("--file", required=True)
    ls.add_argument("--model")
    fi = sub.add_parser("finish")
    fi.add_argument("id")
    fi.add_argument("--verdict", choices=VERDICTS, default="done")
    lv = sub.add_parser("level")
    lv.add_argument("level", choices=[l.lower() for l in LEVELS] + list(LEVELS))
    args = ap.parse_args()

    if args.cmd == "fetch":
        cache = fetch(args.days)
        print(f"кандидатов: {len(cache)} ({sum(1 for c in cache if c['kind'] == 'video')} видео)")
    elif args.cmd == "digest":
        print(digest())
    elif args.cmd == "lesson-digest":
        print(lesson_digest())
    elif args.cmd == "apply":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        items = apply_answer(text, args.model, args.keep)
        print(f"{len(items)} материалов сохранено в french.json")
    elif args.cmd == "lessons":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        print(f"{apply_lessons(text, args.model)} разборов собрано")
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
            print(f"      разбор: {len(les.get('words') or [])} слов, {len(les.get('quiz') or [])} вопросов"
                  if les else "      разбора ещё нет")


if __name__ == "__main__":
    main()
