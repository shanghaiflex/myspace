# Movies — personal film catalog

Pages: `index.html` (home / morning dashboard), `films.html`, `books.html`, `mixes.html`, `lectures.html`, `health.html`. Films page over `movies.json` with posters in `posters/`, served by `serve.py`
(stdlib only) which also exposes a tiny edit API (PATCH/DELETE `/api/movie/<id>`) used by the page for
rating / status / note / delete. Run `./serve.sh` and open http://localhost:8787.
If the page is served by a plain static server the API is absent and the page becomes read-only.

## Editing the catalog

The user changes rating/status/note and deletes films directly in the site. Adding new films is done via
Claude Code with the CLI below. `movies.json` may therefore change outside of git: always `git status`
first, and commit whatever is pending together with your change.

Always use the CLI, never hand-edit `movies.json` unless the CLI can't do it:

```
python3 scripts/movies.py search "query"                     # find OMDb candidates (title, year, imdb id)
python3 scripts/movies.py add "Title" --rating 8              # watched, with my rating (1–10, halves ok)
python3 scripts/movies.py add "Title" --year 1999 --rating 7  # disambiguate by year
python3 scripts/movies.py add tt0162661 --rating 7            # by IMDb id (safest)
python3 scripts/movies.py add "Title"                         # no rating → status to-watch
python3 scripts/movies.py add "Title" --status watching
python3 scripts/movies.py set "Title" --rating 9 --status watched --watched-at 2026-09-06 --note "..."
python3 scripts/movies.py remove "Title"
python3 scripts/movies.py list [--status watched|to-watch|watching]
python3 scripts/movies.py refresh "Title" | --all             # re-fetch OMDb metadata + poster
```

Rules of thumb:
- If the title is ambiguous (remakes, sequels, series vs film), run `search` first and add by `tt…` id.
  Confirm the resolved title/year printed by `add` matches what the user meant; fix with `remove` + `add` if not.
- Rating given → status `watched` (unless `--status` says otherwise). No rating → `to-watch`.
- "I watched X" from the to-watch list → `set "X" --rating N --status watched`.
- Titles are English (OMDb). The user may name films in Russian; translate to the original/English title before searching.
- After changes, commit: `git add -A && git commit -m "Add <title>"` and `git push` (remote: GitHub).
  When the user asks to sync / says they edited on another machine: `git pull --rebase` first.
- Metadata source: OMDb, key in `scripts/movies.py` (override with `OMDB_API_KEY`).

## Books (`books.html`, `books.json`, `scripts/books.py`)

Imported once from the Obsidian vault (`Books/data/*.md`, Dataview `Key:: value` fields). Statuses
`read | reading | to-read | abandoned` (Obsidian's NotFinished), covers in `covers/`. Rating/status/comment
are edited in the site (PATCH `/api/book/<id>`). Adding: `python3 scripts/books.py add "Title" --author "A" [--status read --rating 8]`;
lookup goes Google Books → Open Library (Google rate-limits VPN IPs; Cyrillic titles work only in Google),
so if both fail use `--manual --author ... --year ... --cover URL`. Ids are ISBN-13 when known.

- Every book carries a second title in `titleAlt` (`--title-alt "…"`): the Russian edition title for a foreign
  book, the original title for a Russian one. The card and the sheet show it under the main title, and
  search matches both. Leave it `null` rather than inventing a translation that was never published.
- Cover sources when Google Books gives only a 500-byte placeholder: Open Library
  (`https://covers.openlibrary.org/b/id/<cover_i>-L.jpg`, find `cover_i` via `openlibrary.org/search.json?title=…&author=…`)
  for foreign editions, livelib.ru for Russian ones — search `https://www.livelib.ru/find/books/<query>`
  and take the `boocover` URL out of the result block, bumping `/120x180/` to `/200x305/`.

## Weather (`scripts/weather.py`, `/api/weather`)

Home page shows today's weather: now, day min/max, precipitation (mm, probability, rainy hours) and an hourly strip.
Open-Meteo by default (no key). Set `YANDEX_WEATHER_KEY` in `.env` to switch to Yandex Weather API;
location via `WEATHER_CITY/LAT/LON/TZ` in `.env` (defaults Moscow). Cached 20 min server-side.

## Health (`health.html`, `health.db`, `scripts/health.py`, `scripts/health_review.sh`, `health/`)

Apple Health → the site. The Health Bridge iOS app (repo `~/workspace/healthbridge`, server URL
`https://api.bodywithoutorgans.cc`, bearer token = `HEALTH_TOKENS` in the mini's `.env`) posts HealthKit batches
(workouts, sleep, metrics: hrv_sdnn, resting_heart_rate, steps, active_energy) to `POST /v1/ingest/health/<kind>`;
`serve.py` stores them in `health.db` (SQLite, gitignored, lives on the mini only — like `audio/`). `api.` is an extra
ingress of the same Cloudflare tunnel. Nothing else from the old Go `lifeops` backend is used; it is dead on the mini
(colima broken), its Postgres history was not migrated.

Hourly note: launchd agent `cc.bodywithoutorgans.health` on the mini runs `scripts/health_review.sh` every hour.
It skips when no new samples arrived, otherwise builds `health.py digest` (7-day table, 7/28-day averages, last notes),
prepends `health/PROMPT.md` + `health/goals.md` and pipes it to `claude -p --model opus --tools ""` (Claude Code on the
mini, `~/.local/bin/claude`, auth via `CLAUDE_CODE_OAUTH_TOKEN` in `.env` from `claude setup-token`). The 1–3 sentence
answer is saved to `health.db` (`reviews`) and shown on the home page card and `health.html` (history + 14-day table,
«Обновить» button = `POST /api/health/review`). Goals: edit `health/goals.md` (one per line) and deploy.

```
python3 scripts/health.py digest [--days 7]      # what the model sees
python3 scripts/health.py stats | reviews | summary
sh scripts/health_review.sh --force              # run a note now (on the mini: ssh mini 'cd movies && sh scripts/health_review.sh --force')
ssh mini tail -20 movies/logs/health-review.log
```

## Mixes (`mixes.html`, `mixes.json`)

Morning-mix player: random mix from the collection, "next" button, sources SoundCloud / YouTube /
Mixcloud / NTS (NTS episodes resolve to their SoundCloud or Mixcloud audio). Playback uses the
official embed widgets, so it needs internet; the page controls them via their JS APIs.
The user adds single mixes by pasting a URL into the site (POST `/api/mix`) or by asking Claude Code:

```
python3 scripts/mixes.py add <url>                                  # soundcloud / youtube / mixcloud / nts.live
python3 scripts/mixes.py import-soundcloud <user> [--min-minutes 20] [--dry-run]   # user's public likes, long tracks only
python3 scripts/mixes.py remove <id|url|title>
python3 scripts/mixes.py list
```

- Needs `yt-dlp` (brew) for YouTube/Mixcloud metadata; SoundCloud uses api-v2 with the client_id from yt-dlp's cache.
- The SoundCloud username for imports is not stored anywhere yet; ask the user if unknown.
- Playback position is remembered per mix on the server (`PATCH /api/mix/<id>` `{position}` → `position`,
  `playedAt` in `mixes.json`), so a closed tab reopens on the mix that was playing and continues from there
  («Сначала» in the hero starts over; the position is cleared when a mix plays to the end).
- «Фокус» (or <kbd>F</kbd>) puts the player into real fullscreen with the nav and the collection hidden;
  the hero carries a 25/5 pomodoro (<kbd>P</kbd>), which auto-switches phases with a beep and survives a reload
  (localStorage, resets daily).
- The player hides the SoundCloud/YouTube/Mixcloud widgets off-screen and draws its own progress bar
  (seek via widget APIs). Page background: random photo from `backgrounds/` (listed by `/api/backgrounds`),
  crossfades on every new mix. The user can drop their own JPG/PNG there.
- Mix ids: `sc:<n>`, `yt:<videoId>`, `mc:<uploader_slug>`. Fields: source, url, title, artist, duration (s),
  artwork, genre, published, tags[], addedAt, optional `nts` (episode URL), `position`/`playedAt` (resume).

### Daily advice from Claude (`mix_recs.json`, `mixes/PROMPT.md`, `scripts/mix_recs.py`, `scripts/mix_recs.sh`)

Once a day the mini asks Claude Code for new mixes. `mix_recs.py digest` = collection + what was actually
played (position/playedAt) + verdicts on earlier advice; `mixes/PROMPT.md` asks for 6 candidates as JSON
(`search`, `artist`, `title`, `why`); `mix_recs.py apply` resolves them through SoundCloud search
(`/search/tracks`, ≥20 min, the asked-for artist must really be in the track, nothing already known) and
keeps the first 3 in `mix_recs.json` (`items` + `history`). The mixes page shows them as «Советует Claude»
cards (Послушать / Нравится / Не то) and the home page uses the top one as «Микс на сегодня».
Feedback is the whole point: «Нравится» adds the mix to the collection (tag `claude-rec`), «Не то» is a
rejection, playing one for a minute is an implicit signal — all three land in `history` and go into the
next day's prompt.

```
python3 scripts/mix_recs.py digest | list        # what the model sees / current advice
sh scripts/mix_recs.sh --force                   # ask for a new batch now (ssh mini 'cd movies && sh scripts/mix_recs.sh --force')
python3 scripts/mix_recs.py verdict <sc:id> liked|dismissed|played
ssh mini tail -20 movies/logs/mix-recs.log
```

launchd agent on the mini: `cc.bodywithoutorgans.mixrecs`, daily at 07:20 (`deploy/install-mix-recs.sh`).
API: `GET /api/mix-recs`, `PATCH /api/mix-rec/<id>` `{verdict}`, `POST /api/mix-recs/refresh` (the «Обновить»
button on the mixes page).

## Lectures (`lectures.html`, `lectures.json`, `scripts/lectures.py`)

Tracks lecture channels: YouTube «Семинары по истории Александра Макарова» (mostly medieval everyday
life) and SoundCloud «Serj Bushwacker» (world history, ids `sc:<trackid>`). Statuses
`new | queued (в планах) | listening | listened`, custom series via `lectures.py series "<name>" <ids>`
(e.g. «Древний Египет» = 7 Bushwacker lectures the user plans to re-listen, in chronological order), playback position is saved to the server, so the
phone and the laptop share progress. Playback: audio-only file from `audio/<id>.m4a` if downloaded
(works on the phone with the screen locked, Media Session controls), otherwise a hidden YouTube iframe.
Recommendations (`rec.js`) rank unlistened lectures by shared playlists/keywords with listened ones.

```
python3 scripts/lectures.py sync                       # refresh channel videos + streams + playlists (minutes)
python3 scripts/lectures.py set "<title|id>" --status listened|listening|new [--position SEC]
python3 scripts/lectures.py audio "<title|id>" ...     # download m4a into audio/ (gitignored, per machine)
python3 scripts/lectures.py list [--status ...]
python3 scripts/lectures.py add-channel <url>
```

- When the user says "послушал лекцию про X" → find it with `list`/grep and `set --status listened`.
  "Хочу послушать X" → `set --status queued` (queue order = time of queuing, shown on the home page).
- `sync` re-fetches both channels; YouTube playlists take minutes, use `--no-playlists` for a quick refresh.
- Audio files live only on the machine that runs the server (the Mac mini in production): download there.
- Home page shows one card per lecture channel (channel `label` in lectures.json, e.g. «Макаров · Средневековье»,
  «Bushwacker · Древний Египет»: next up + queue/recommendations), a random mix, random to-watch posters.

## Editing workflow (IMPORTANT — how to ship changes)

When the user asks to change anything (add/remove/rate a film, book, mix, lecture; edit a page; etc.):
1. Make the change on the laptop (the CLIs: `scripts/{movies,books,mixes,lectures}.py`, or edit the HTML/py).
2. Run `scripts/deploy.sh "short message"`. This commits, pushes to GitHub, and updates the live mini in one step.
   Do this automatically after the edit — the user expects the change to be live at https://bodywithoutorgans.cc.
`deploy.sh` pulls the mini's live JSON for any catalog file you did NOT edit this session first, so in-site
edits (ratings/status changed through the website) are never clobbered. If the mini is unreachable it still
commits+pushes and tells you it skipped the deploy. Data files (`*.json`) need no restart; code changes get one
automatically. Audio lives only on machines (gitignored) — download lecture audio on the laptop; deploy pushes it to the mini.

## Auth & deploy
`serve.py` requires a password when `MOVIES_PASSWORD` is set (env or `.env`, gitignored); localhost runs open.
Production: https://bodywithoutorgans.cc served by the home Mac mini (ssh alias `mini`, user sergeyfilatov,
site in `~/movies`, launchd agents `cc.bodywithoutorgans.serve` / `.tunnel`), LIVE since 2026-09-07 at https://bodywithoutorgans.cc. Three services on the mini: serve (agent), a root VPN
LaunchDaemon `cc.bodywithoutorgans.vpn` (`/usr/local/sbin/awg-mini.sh` runs a headless AmneziaWG full tunnel
via the mini's OWN Server 1 config 10.8.1.5 — the ISP kills direct Cloudflare, the VPN's path is clean), and
cloudflared (agent, http2). See `deploy/README.md`.
The mini has no git/brew/CLT; Python lives in `~/.local/python312`, tools in `~/bin`, Claude Code in `~/.local/bin/claude`
(native install, no node needed; `python3` is NOT on the default ssh PATH — use `~/.local/python312/bin/python3`). Data sync is rsync:
after ANY change run `scripts/deploy.sh "message"` — it folds in live in-site edits for JSON you didn't touch,
commits, pushes to GitHub, rsyncs to the mini, and restarts serve. That is the one command to ship to production.
The mini reaches SoundCloud/Cloudflare/Open-Meteo directly but YouTube is blocked there without a VPN (its AmneziaVPN
is currently not configured), so download lecture audio on the laptop (`scripts/lectures.py audio <id>`) and let
`scripts/sync.sh` push `audio/` to the mini.

## Laptop
The always-on local instance on http://localhost:8787 is OFF since 2026-09-07: production went live, so
the laptop no longer needs its own copy running. Bring it back with `sh deploy/local-docker.sh` (colima +
`docker run`, repo bind-mounted, image `movies:local` is still built); `docker rm -f movies` turns it off
again. `docker-compose.yml` describes the same container but the compose plugin is not installed here —
use the script. The Firefox homepage is production, https://bodywithoutorgans.cc (it was localhost until
the tunnel went live), set in the profile's `user.js`
(`~/Library/Application Support/Firefox/Profiles/oswx4c6l.default-release/user.js`).
The user wants it on startup and the Home button ONLY — new tabs must stay Firefox's own new-tab page.
Do not install a new-tab-override extension for this: «New Tab Homepage» was tried, made every new tab
open the catalog, and is now disabled (`extensions/{66E978CD-…}.xpi.disabled`).

## Data model (`movies.json`, array sorted by title)
`id` (imdb), `title`, `year`, `director`, `genre[]`, `runtime` (min), `plot`, `imdbRating`,
`poster` (local path), `posterUrl`, `type` (movie|series), `status` (watched|to-watch|watching),
`rating` (my 1–10 or null), `addedAt`, `watchedAt`, `note`, optional `link`.

## Origin
Imported once from the Obsidian vault (`~/Documents/Obsidian Vault/Movies/data`) via
`scripts/import_obsidian.py`. This repo is now the source of truth; Obsidian is not synced.

## Еда (`pantry.html`, `pantry.json`, `scripts/pantry.py`, `scripts/pantrylib/`)

Инвентаризация продуктов из фискальных чеков в почте. Источник — электронные чеки ОФД,
практически целиком ВкусВилл (~500 чеков с 2021 года, 4 в неделю). Читаются по IMAP, доступ только на чтение.
Пароль приложения Gmail: на ноутбуке в связке ключей (`security add-generic-password -a <адрес>
-s gmail-imap-mcp -w`), на mini — в `.env` как `GMAIL_APP_PASSWORD` (связка ключей на headless-машине
запирается после перезагрузки без входа в систему). Google показывает пароль **с пробелами** —
в `.env` их надо убрать, иначе `set -a; . ./.env` возьмёт только первое слово и переменная
не экспортируется, молча.

Конвейер в `scripts/pantrylib/` разделён по потоку данных: `gmail_tool` (почта) → `receipts`
(два разных шаблона писем ОФД → позиции) → `analyze` (частота перекупок → расход в день) →
`shelf_life` (ключевое слово → категория) → `inventory` (остатки, предложение докупить) и
`photos` (фото блюда). Всё детерминированное; модель пишет только идеи блюд.

**Ежедневной заметки больше нет** (убрана 2026-09-07 по просьбе пользователя): страница начинается
с идей блюд, вызов модели ради текста, который никто не читал, был выброшен вместе с `pantry/PROMPT.md`.

**Списка «съесть скорее» намеренно нет.** Он был и врал: чек фиксирует покупку, но не то, что
съедено, поэтому система звала доедать камамбер, съеденный неделю назад. Срок годности всё ещё
считается — он отделяет еду от пакетов и авансов и решает, считать ли пачку вскрытой, — но
пользователю не показывается. Не возвращай его без ручного ввода «съел это».

```
python3 scripts/pantry.py sync [--all]   # забрать новые чеки (--all: перечитать всё)
python3 scripts/pantry.py summary        # пересчитать остатки → pantry.json
python3 scripts/pantry.py digest         # текстовый контекст для заметки
python3 scripts/pantry.py profile        # профиль покупок — контекст для идей блюд
sh scripts/pantry_review.sh [--force-recipes]   # весь цикл: почта → расчёт → идеи блюд
```

Чего в данных нет и что поэтому является догадкой: **срок годности** (в чеке его нет,
берётся из `scripts/pantrylib/data/shelf_life.json` по категории — правь эту таблицу руками,
когда оценка разойдётся с реальностью) и **расход** (никто не знает, что съедено; он выведен
из того, как часто товар перекупается). Вес есть в названии примерно у половины позиций,
у весовых товаров количество и есть вес в килограммах.

Предложение докупить намеренно молчаливое — пороги вынесены константами наверху
`scripts/pantrylib/inventory.py`. Товар попадает в список, только если покупался ≥3 раз,
цикл ≤45 дней, прошло ≥80% цикла, запас кончается в ближайшие 3 дня, просрочка не больше
одного цикла и месяц попадает в сезон прошлых покупок (иначе арбуз предлагается в сентябре).
Если система станет навязчивой — крутить `DUE_FACTOR` и `HORIZON_DAYS`.

### Идеи блюд (`pantry/RECIPES.md`, `scripts/pantrylib/photos.py`, `dishes/`)

Раз в несколько дней (`RECIPES_STALE_DAYS`, или `pantry_review.sh --force-recipes`) Claude получает
профиль покупок (`pantry.py profile`: доли категорий + регулярные товары за все годы, плюс что уже
на странице и что отклонено) и возвращает JSON ровно на столько блюд, сколько не хватает до четырёх
(`pantry.py recipes-need`), — они дописываются к оставшимся (`recipes-save --append`).

**Обратная связь.** «Убери блюдо X» → `python3 scripts/pantry.py reject "X"` (на mini, это production):
блюдо исчезает со страницы, его фото удаляется, а название попадает в `rejected` и уходит в промпт
как «не предлагай снова и близкое тоже». Как вердикты у микс-советов — без этого модель предлагает
одно и то же по кругу. Первый отзыв (2026-09-07): сырники и жареные пельмени — «это я и сам могу
приготовить», отсюда правило в `RECIPES.md` не предлагать очевидное. Ограничения жёсткие и намеренные: ≤6 ингредиентов, ≤4 шага, ≤20 минут — по чекам
видно, что человек покупает много готового, длинные рецепты мимо.

Фото ищутся цепочкой: **Pexels** (лучшая съёмка, нужен бесплатный ключ в `PEXELS_API_KEY`) →
**TheMealDB** (студийные фото, ключа не нужно, но каталог маленький и западный) → **Wikimedia
Commons** (всегда работает, но снимки в основном любительские). Openverse отвергнут: режет
анонимные запросы.

**Фото выбирает модель, глядя на них — в два прохода.** Первый: `claude -p --tools ""` придумывает
блюда (инструментов нет вовсе). Затем скрипт скачивает по 6 кандидатов на блюдо
(`pantry.py photo-options`). Второй: `claude -p --tools Read --add-dir <cand>` открывает каждый файл,
смотрит и возвращает JSON «блюдо → имя файла». Bash модели не даётся намеренно: это фоновая задача
на домашней машине, а для выбора картинок руки не нужны — только глаза. Промпт второго прохода —
`pantry/PHOTOS.md`.

Если модель забраковала всех кандидатов (`null`), фото не ставится вовсе и карточка показывает
заглушку «подходящего фото не нашлось». Автоподбор в этом случае **не включается**: подпись обещает
иллюстрацию именно к этому блюду, а именно автоподбор и подсовывал не то. Он остаётся как запасной
путь, только если второй проход не отработал совсем.

Правила автоподбора (`photos.py`, запасной путь) выстраданы на промахах и все нужны: снимок отбрасывается, если в названии есть
**форма блюда, не упомянутая в запросе** (иначе «creamy mushrooms with bacon» → карбонара);
общие слова (`STOP`: creamy, sauce, fresh…) не считаются совпадением (иначе «пельмени в сливочно-
грибном соусе» → банка зелёного соуса); у MealDB требуется совпадение минимум по двум значащим
словам. Запрос к Commons укорачивается по словам, но кандидаты **накапливаются со всех попыток**,
а не берутся с первой непустой — «dumplings in creamy mushroom» находит ровно одно фото витрины
с меню, а «dumplings» — дюжину нормальных. Второй проход с ослабленными фильтрами лучше пустой
карточки. Ответы кэшируются (`data/commons_cache`), пауза между запросами 3 с.

Фото подписано как иллюстрация, а не снимок этого блюда, — это правда, и CC требует атрибуции.

`dishes/`, `pantry.json`, `data/receipts.json` и кэш — данные машины, не репозитория: в `.gitignore`
и в исключениях `deploy.sh`, как `audio/`. Рецепты генерирует mini, снимки качает он же.

Расписание на mini: `sh deploy/install-pantry.sh` (launchd, ежедневно в 09:30).
Кнопка «Обновить» на странице дёргает `POST /api/pantry/review`.
