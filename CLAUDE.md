# Movies — personal film catalog

Pages: `index.html` (home / morning dashboard), `films.html`, `books.html`, `mixes.html`, `lectures.html`, `health.html`, `reads.html`, `french.html`, `pantry.html`, `home.html` (умный дом). Films page over `movies.json` with posters in `posters/`, served by `serve.py`
(stdlib only) which also exposes a tiny edit API (PATCH/DELETE `/api/movie/<id>`) used by the page for
rating / status / note / delete. Run `./serve.sh` and open http://localhost:8787.
If the page is served by a plain static server the API is absent and the page becomes read-only.

**Оформление.** Общая палитра и типографика сайта — `theme.css` (18.09.2026, журнальный стиль в духе The
New Yorker; до этого была кремовая бумага с терракотой). Белая бумага, чёрная антиква, киноварь `#c0362a`
для рубрик и текущего раздела, волосяные линейки вместо теней, прямой угол вместо скруглений. Каждая
страница держит свою вёрстку в inline `<style>` и подключает `theme.css` последним, так что его `:root`
переопределяет одноимённые токены (`--bg`, `--surface`, `--text`, `--line`, `--accent`, `--ok`, `--glass`,
`--shadow`, `--card`, `--photo-veil`, `--radius`…), а правила после `:root` — вид того, что есть на всех
страницах: полоса меню, заголовки, карточки, кнопки, чипы, таблицы. Новую страницу делай на тех же токенах
и подключай `theme.css`; цвета и гарнитуры в самих страницах не правь — только в `theme.css`.
**`home.html` тоже подключает `theme.css`** (18.09.2026): раньше «Дом» был источником стиля и держал палитру
у себя, теперь источник один. Свои токены у него остались только на скетч кухни (`--sky`, `--blend`).

Три гарнитуры, все с кириллицей (Fraunces её не имел вовсе — русские заголовки молча падали в Georgia):
`--display` Playfair Display (заголовки страниц, карточек и статей — замена журнальному Irvin, он
лицензионный), `--serif` Spectral (весь текст, замена Caslon: у Caslon-клонов либо нет кириллицы, либо
крошечный рост строчных), `--sans` Jost (рубрики, кнопки, шапки таблиц, меню — прописными с разрядкой,
замена Neutraface). Грузятся из Google Fonts через `@import` в `theme.css`; если сети нет, fallback —
Georgia / New York, то есть страница остаётся серифной.

Два правила в `theme.css` сделаны с намеренно высокой специфичностью, и это не случайность: страницы
задают радиусы и у `.card`, и у `.card.wx.c4`, поэтому «прямой угол на всю страницу» написан цепочкой
`:not()` (каждый класс в цепочке добавляет вес; перечисление внутри одной скобки — нет), а тени снимаются
селекторами с удвоенным классом (`.card.card .thumb`). Круглым осталось только круглое по смыслу: кнопка
«играть», ползунки, тумблеры, точки.

**Для графиков там же лежит палитра `--c1…--c6` плюс нейтральный `--c0`** (18.09.2026). Слоты назначаются
подряд и никогда по кругу — порядок и есть защита от дальтонизма. Валидатор теперь лежит в репозитории:
`python3 scripts/palette_check.py` (OKLab, симуляция Machado 2009), и его отчёт — это и есть цифры ниже.
Набор проходит все шесть проверок: светлота в полосе, цветность выше пола, худшая соседняя пара ΔE 20,1
при полном зрении и 12,4 при дальтонизме (протанопия и дейтеранопия; тританопия печатается справкой и
порогом не является — её не проходил и прежний набор), контраст к бумаге ≥ 3,25:1. `--c1` — та же киноварь,
что у рубрик, поэтому из-за неё `--c2` из пурпура стал синим: с киноварью пурпур расходился всего на 13,9
при полном зрении. Один и тот же набор работает в обеих темах, поэтому в тёмной он не переопределяется.
Новые цвета для данных заводить можно и нужно — но только здесь и только прогнав валидатор заново:
прежняя пара «терракота + шалфей» расходилась всего на 14,9 и была неразличима даже при полном зрении.

```
python3 scripts/palette_check.py                       # палитра из theme.css
python3 scripts/palette_check.py '#c0362a' '#2f6fb5' …  # проверить свой набор до правки
```

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
lookup goes Google Books → Open Library → livelib.ru (Google rate-limits VPN IPs, Open Library drops connections
from the mini, livelib is the one source that reliably answers from the mini and knows Russian editions — but it
gives no ISBN/year, only title/author/cover). If all fail use `--manual --author ... --year ... --cover URL`. Ids are ISBN-13 when known.

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

Apple Health → the site. The BoW iOS app (repo `~/workspace/bow`, until 2026-09-14 «Health Bridge» in
`~/workspace/healthbridge`; bundle `cc.bodywithoutorgans.bow`, server URL `https://api.bodywithoutorgans.cc`,
bearer token = `HEALTH_TOKENS` in the mini's `.env` — the same token now also opens every `/api/` and `/audio/`
route, see `logged_in()` in `serve.py`) posts HealthKit batches
(workouts, sleep, metrics: hrv_sdnn, resting_heart_rate, steps, active_energy) to `POST /v1/ingest/health/<kind>`;
`serve.py` stores them in `health.db` (SQLite, gitignored, lives on the mini only — like `audio/`). `api.` is an extra
ingress of the same Cloudflare tunnel. Nothing else from the old Go `lifeops` backend is used; it is dead on the mini
(colima broken), its Postgres history was not migrated.

Every ingest request carries `X-Trigger` (foreground / healthkit:<type> / bg-refresh / manual) and the mini logs it
(`health ingest metrics from iphone (healthkit:StepCount): …`) — that line is the proof that iOS woke the app in
the background. If only `foreground`/`manual` ever show up, background delivery is not working: check «Обновление
контента» for BoW in iOS Settings and that the app was not swiped away from the switcher. The app itself keeps a
journal (tab «Здоровье») of launches, syncs and downloads.

**Signing / «посинкай приложение».** The Apple ID is a free Personal Team (8NCN36FYGA): the provisioning
profile lives 7 days, after which the app refuses to launch (no background sync, no lectures) until it is rebuilt
and reinstalled. When the user says «посинкай/обнови приложение (на телефоне)» run `sh ~/workspace/bow/phone.sh`
— it builds, installs and launches over the cable or over Wi-Fi (the phone is paired for network use; the script
prints how it is reached and when the new profile expires; `--check` only probes). Reinstalling kills downloads in
progress, so don't reinstall for fun. The only way out of the weekly ritual is the paid Apple Developer Program.

**Two routes to the mini.** Without a VPN the home ISP drops Cloudflare, so the app cannot reach
bodywithoutorgans.cc from the home Wi-Fi. `serve.py` on the mini therefore listens on the LAN too
(`MOVIES_BIND=0.0.0.0` in the mini's `.env`; default is localhost) and the app probes
`http://192.168.1.40:8787/healthz` (Settings → «Адрес mini в домашней сети», 1.5 s, cached a minute) before
every sync/refresh and uses it when it answers — otherwise the internet address. The journal logs «Маршрут» on
every switch. If the mini's LAN IP changes, fix it in the app's Settings and in `SettingsStore` default.

Вкладка «Сайт» на домашней сети требует **отдельного разрешения ATS**: `NSAllowsLocalNetworking` в `Info.plist`
покрывает только `URLSession`, а содержимое `WKWebView` под него не попадает. Симптом обманчивый — синк, лекции
и здоровье по `http://192.168.1.40:8787` работают, а сайт открывается пустым, и на mini при этом **нет ни одного
запроса** `/app/login`: страницу не выпускает сам iOS, до сервера дело не доходит. Лечится ключом
`NSAllowsArbitraryLoadsInWebContent` (2026-09-14, он влияет только на вебвью). Ошибка загрузки страницы теперь
уходит в журнал приложения («Сайт не открылся» + адрес и причина) — пустая вкладка иначе не оставляет следов.

**The site inside the app.** Каждый таб — WKWebView на https://bodywithoutorgans.cc, залогиненный через
`GET /app/login?t=<bearer>&next=<путь>` (токен → обычная сессионная cookie). Pages know they run in the app
(`window.BoW`, user agent contains `BoW/2`); `lectures.html` hands lectures that are downloaded on the phone
(`window.BoW.downloaded`) to the native player through `webkit.messageHandlers.bow` (`{type:'play', id, position}`).
Everything else is the plain site. New phone-specific behaviour goes the same way: a small hook in the page, the
heavy part native.

**Родные экраны приложения — та же журнальная тема** (18.09.2026). `Theme.swift` в `~/workspace/bow`
повторяет тёмную половину `theme.css` теми же hex (чёрная бумага `#131210`, киноварь `#e3604d`), карточки
стали прямоугольными с волосяной рамкой, заголовки экранов и лекций — системной антиквой New York (своего
Playfair в приложении нет и не нужно: New York того же рисунка, приезжает с iOS и умеет динамический кегль),
рубрики — прописными с разрядкой киноварью (`.sectionLabel()`). Заголовки навигации и таб-бар
переопределяются один раз при запуске (`Theme.configureAppearance()` из `AppDelegate`). Правишь палитру на
сайте — поправь и здесь, иначе вебвью и родные экраны разъедутся.

**Табы внизу — весь сайт** (17.09.2026, порядок задан пользователем). iOS показывает пять табов и не больше:
шестой он сам уносит в системное «More», которое мы не упорядочиваем и не оформляем, — поэтому пять и есть:
«Главная», «Лекции» (нативный плеер), «Миксы», «Дом» и «Ещё». В «Ещё» (`MoreView.swift`) списком лежит
остальной сайт — «Французский», «Фильмы», «Книги», «Почитать», «Well-being» — и два своих экрана приложения:
«Синк и журнал» (прежний таб «Здоровье»: что ушло на mini и что делала iOS в фоне) и «Настройки».
**«Еда» (`pantry.html`) в приложении не показывается вовсе** — ни таба, ни строки в «Ещё» (просьба
пользователя 17.09.2026). Страница живёт своим списком: `SiteController.Page` (id = `window.BoW.tab`, путь,
название, SF Symbol), `SiteController.controller(for:)` держит по вебвью на страницу, а `SitePage` создаёт его
**на первом заходе** — иначе TabView поднял бы весь сайт сразу на старте. Позиция в миксах и лекциях поэтому
переживает переключение табов: вебвью не умирает.

**Страница кончается там, где начинается таб-бар.** Вебвью не игнорирует нижнюю безопасную зону (17.09.2026):
пока игнорировал, низ страницы уезжал под бар — во французском под ним оказывались кнопки листа с разбором,
и нажать их было нельзя.

**Лекции записаны тихо**, а громкость AVPlayer выше системной не поднимается, поэтому «Громче» (1× / 1,5× /
2× / 2,5×) — это `AVAudioMix` на дорожке, вторая секция в меню скорости в плеере (`PlayerEngine.gain`,
запоминается). Проверено экспортом куска лекции: ×2 — это +6 дБ по RMS и клиппинг на 0,09% отсчётов
(речь с большим крест-фактором, пики редкие), ×2,5 — уже 0,4%, слышно на взрывных.

**Вебвью, ушедший из окна, замолкает.** WebKit ставит на паузу всё, что в нём играло, а переключение таба
ровно это и делает: вид выбранного таба в окне, остальные — нет (микс обрывался на полуслове). Поэтому таб
держит `SiteBox`, а вебвью ему только сдаётся: уходя с экрана, бокс парковывает его в самом окне — за всем,
почти прозрачным, со своим прежним размером, чтобы страница не перекладывалась и виджет SoundCloud не
дёргался, — а вернувшись, забирает назад (`didMoveToWindow`, без участия SwiftUI). Аудиосессия у приложения
`.playback` и фоновый режим `audio` уже есть от лекций, так что микс играет и с погашенным экраном.

**Меню сайта внутри приложения скрыто** (`app.js`, подключён во всех страницах после `theme.css`): когда у
каждой страницы свой таб, `.nav` ведёт туда же, но мимо табов — и показывает «Еду». Скрывает стилем сразу,
до отрисовки (иначе меню мигает), а полосу, в которой кроме меню ничего не было (главная, Well-being,
Почитать, Дом), убирает целиком. Где в полосе есть своё (фильтры фильмов и книг, поиск лекций, плюс в миксах,
уровень во французском) — полоса остаётся без меню. Раньше это делал частный случай в `home.html`; его больше нет.

**Уведомления на телефон и часы** (18.09.2026). Пуша нет и не будет: APNs даёт только платная программа, —
поэтому уведомление ставит сам телефон. `GET /api/updates` (`scripts/notify.py`) отдаёт то, о чём стоит
зазвонить прямо сейчас, а `Notifier.swift` показывает локальное уведомление на всё, чего ещё не видел (id
показанного лежит в `UserDefaults`; свежесть — забота сервера, поэтому после переустановки телефон в
худшем случае повторит то, что и так актуально). Apple Watch зеркалят уведомление телефона сами, пока он заперт, так что
приложения для часов не нужно; в iPhone → Watch → Уведомления у BoW должно стоять «Повторять с iPhone».
Проверка идёт там же, где синк: запуск, выход на экран, BGTask и каждый приход данных из HealthKit (не чаще
раза в две минуты), — отсюда и задержка: уведомление приходит, когда iOS в очередной раз будит приложение,
а не в секунду появления записи. Тумблер «Уведомления» — в настройках приложения.

Что отдаётся: **каждая новая заметка о самочувствии** (просьба пользователя 18.09.2026; ночная — нет, её и
страница не показывает, старше `REVIEW_FRESH_HOURS` = 3 ч — тоже, телефон мог не просыпаться полдня) и
**одна сводка советов в день** (`recs:<дата>`, перечисляет, что обновилось). Советы приходят четырьмя
заходами с 07:20 до 08:10, и уведомлять о каждом — это четыре звонка за час; поэтому сводка собирается
после `RECS_SETTLE` = 08:30, когда все агенты отработали. Тап по уведомлению открывает страницу: заметка —
Well-being, сводка — главную.

```
python3 scripts/notify.py list      # то, что увидит телефон
```

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

Новая тренировка запускает заметку сразу, не дожидаясь часового агента: в обработчике `/v1/ingest/health/workouts`
серве́р видит, сколько строк реально вставилось, и при `inserted > 0` дёргает `start_review_job` с паузой
`WORKOUT_REVIEW_DELAY` (90 с). Пауза нужна, потому что приложение шлёт тренировку и её калории разными пачками —
заметка без паузы рассказывала бы про тренировку, сжёгшую ноль. Повторная отправка не считается: она даёт
`updated`, а не `inserted`, и ничего не запускает. Метрики и сон тоже не запускают — только тренировки.

**Сон: стадии сырыми, ночи собирает сервер** (2026-09-14). До этого дня приложение складывало стадии по
календарным суткам и слало одну сводку со свежим UUID на каждый синк — переустановка 14.09 перечитала историю,
и та же ночь легла в базу трижды: в дайджесте стояло «сон 20ч01», среднее за неделю 16ч57, и все заметки писались
по этим цифрам. Вдобавок ночь, начатая до полуночи, попадала в те же сутки, что и вечернее засыпание следующего
дня, — примерно 6% записей были слепком на сутки, разобрать который уже нельзя. Теперь `SleepAssembler` отдаёт
`HKCategorySample` один к одному (`stage` = rem/deep/core/asleep/awake/inBed, `id` = uuid образца, то есть
повторная отправка — это update), а `health.py` собирает ночи сам: отрезки ближе `SLEEP_GAP` (3 ч) — одна ночь,
сон = rem+deep+core (минуты бодрствования в кровати сном не считаются), пересечения двух источников берутся
объединением интервалов, а не суммой. Ночь, закончившаяся после `SLEEP_EVENING` (18:00), — это не ночь, а начало
следующей (`sleep_key`), и если у утра нет ничего, кроме такого куска, сон помечается неизвестным, а не «1ч24».
Старые сводки (`kind = "sleep"`) продолжают читаться, но только там, где ночь не покрыта стадиями, — двойного
счёта нет. Якорь сна в приложении переименован (`sleepStages`), поэтому новая сборка перечитывает год заново
страницами по 2000 образцов. Типы тренировок, которых нет в switch приложения, приходили строкой
`HKWorkoutActivityType(rawValue: 48)` — теннис, силовая, кор, HIIT, триатлон были для модели безымянны;
`HK_RAW` в `health.py` переводит их по номеру (и switch в приложении расширен).

Часы носятся не каждый день, и это не аномалия. Сон, HRV и пульс покоя приходят только с Apple Watch, а без них
активные калории падают до оценки телефона (единицы ккал при нормальных шагах — шаги считает сам телефон).
`daily()` помечает такой день `noWatch` (есть шаги/ккал/тренировка, но нет сна, нет HRV и ккал < `NO_WATCH_KCAL`),
в дайджесте он идёт с пометкой «← без часов», и такие дни исключены из средних по сну/HRV/RHR/ккал (шаги и
тренировки считаются по всем дням). `health/PROMPT.md` велит модели молча пропускать такие дни и не просить
надевать часы. День вообще без данных под пометку не попадает — это пустой день, а не голое запястье.

Ночная заметка утром не показывается. `summary()` помечает `review.stale`, если заметка написана раньше
последнего «утра» (`MORNING_HOUR` = 6 утра по местному); главная и `health.html` тогда её не выводят —
на `health.html` она уезжает в «Раньше», а карточка предлагает дождаться свежей. Совет «ложись спать»
за завтраком читать нечего, а новая заметка появляется, как только утром придут первые данные с телефона.

Бэкап: `health.db` — единственная копия данных на mini (Time Machine там не настроен), а телефон повторно
не присылает то, что уже отдал (анкеры HealthKit в `UserDefaults`, кнопки сброса в приложении нет), поэтому
потерянную базу восстановить нечем — 2026-09-07 она пропала и вернулась только потому, что переустановка
приложения обнулила анкеры. Отсюда ночной снимок: launchd `cc.bodywithoutorgans.healthbackup` (`deploy/install-health-backup.sh`)
в 04:10 запускает `scripts/health_backup.sh` → `health.py backup` (онлайн-бэкап SQLite, безопасен при работающем serve)
в `backups/health-YYYY-MM-DD.db`, хранит 7 последних. `backups/` — данные машины: в `.gitignore` и в исключениях
`deploy.sh`, как `health.db` и `audio/`.

```
python3 scripts/health.py backup [--keep 7]      # снимок вручную
ssh mini tail -5 movies/logs/health-backup.log
```

## Расписание (`scripts/schedule.py`)

Заметка о здоровье знала тело, но не знала день: могла звать гулять в занятый час и не видела, что завтра рано
вставать. `schedule.py` читает Яндекс.Календарь, сам разворачивает RRULE (weekly/daily/monthly/yearly, EXDATE,
переносы отдельных встреч через RECURRENCE-ID) и отвечает на два вопроса: что сегодня и где в дне дыра.
Кэш — `data/calendar.ics`, 30 минут, при сбое сети берётся старая копия (данные машины: в `.gitignore` и в
исключениях `deploy.sh`). Файл назван `schedule.py`, а не `calendar.py`, потому что второе имя затеняет
стандартный модуль и ломает `strptime`.

**Читается через CalDAV, а не через ссылку экспорта.** Экспорт (`YANDEX_CALENDAR_ICS`) публикует ровно один
календарь и указывал на «Мои события» — заброшенный в апреле 2026; живой календарь — «Семья» (332 события),
там и врачи, и няня, и «теннис Серёжи». Час по CalDAV (`YANDEX_CALDAV_USER` = `ssfilatov94`,
`YANDEX_CALDAV_PASSWORD` = пароль приложения с id.yandex.ru → Безопасность → Пароли приложений → Календарь)
забирает все коллекции сразу; ссылка экспорта осталась запасным путём на случай, когда пароля нет.

Календари: «Мои события» (повторяющиеся правила быта — единственный, что идёт в заметку), «Семья» (общий с
Полиной, 332 события: врачи, няня, дни рождения — читается, но в промпт не попадает), «Тренировки» (плавание
Полины), «Не забыть» (todo, пропускается). `SCHEDULE_CALENDARS` в `.env` — белый список того, что видит модель
(сейчас `"Мои события"` — значение с пробелом обязательно в кавычках, иначе `set -a; . ./.env` в
`health_review.sh` спотыкается на втором слове: `события: command not found`; пусто = все календари); списков чужого времени **два, и это разные вещи** (20.09.2026):
`SCHEDULE_FREE_CALENDARS` (по умолчанию `Тренировки,Семья`) — показывать, но временем не занимать, а
`SCHEDULE_HIDE_CALENDARS` (по умолчанию `Тренировки`) — не показывать вовсе. До этого дня список был один, и
«показывать, но не занимать» было нельзя: `day_plan()` выбрасывал те же календари, что и `free_windows()`.
**«Семья» с 20.09.2026 показывается, но время не занимает** (просьба пользователя): это общий календарь, и почти
всё в нём заводит Полина под своим `ORGANIZER` — её встреча с друзьями вычёркивала из моего дня четыре часа.
Плашка рисует такое событие курсивом и подписывает именем владельца (`ПОЛИНА` рамочкой перед заголовком),
окно оно не режет — в том числе врачи и няня; если заметка позовёт гулять в час приёма, вот почему. Плавание
Полины по-прежнему не показывается: четыре часа чужой тренировки — не дело дня.

**Чьё событие, решает `ORGANIZER`, а не календарь** (20.09.2026). В «Семье» 322 события завела Полина и 10 —
я сам (баня, теннис, «привезут пол»), и по календарю их не различить: пометив «Семью» свободной, я заодно
освободил бы свои десять. Поэтому событие несёт `who` — имя владельца, если он не я, — и **своё событие в общем
календаре остаётся занятым**. Имя берётся из `SCHEDULE_PEOPLE` в `.env` (`почта=Имя` через запятую), а не из
`CN`: Яндекс кладёт туда логин (`polya.m-d-g-g`), и в плашке это читается как мусор. Кто «я» — `SCHEDULE_ME`,
по умолчанию `YANDEX_CALDAV_USER@yandex.ru`. Незнакомая почта именем не подписывается, но время занимать
перестаёт (она не я), так что новому человеку в общем календаре нужна строка в `SCHEDULE_PEOPLE`.
**Флаг «свободен» (`TRANSP:TRANSPARENT`) временем больше не разжимает** (2026-09-15). Он значил ровно обратное
тому, как читался: в Яндексе им помечают час, который можно перекрыть чужим приглашением, а не час, когда
человека нет на месте. «Французский Сережи» стоит с этим флагом — и заметка звала в бассейн «с 18:00 и до
ночи», не заметив, что в 19 занято. Теперь окно режут все события, кроме чужих календарей
(`SCHEDULE_FREE_CALENDARS`); занятость определяется только принадлежностью к календарю.
События помечаются источником (`X-BOW-CALENDAR`) при сборке: `CATEGORIES` Яндекс заполняет только в
одном календаре. Свободное окно — дыра не меньше `FREE_MIN` (45 мин) между `DAY_START` и `DAY_END`.

**Кэш держит все календари, отбор — у читателя** (2026-09-14). `SCHEDULE_CALENDARS` раньше применялся при
загрузке, поэтому «Семья» в файл вообще не попадала. Теперь `caldav_ics()` забирает все коллекции кроме todo,
а сужает `agenda(only=…)`: заметка видит только `SCHEDULE_CALENDARS`, а плашка «Дела на сегодня» на главной —
все календари, кроме чужих тренировок (`agenda(skip=free_calendars())`), потому что врачи и няня из «Семьи» —
это дела дня, а четыре часа плавания Полины делом не являются. `schedule.py list` помечает событие календарём.

`day_plan()` (`schedule.py today`, `GET /api/schedule`, кэш в serve 10 минут поверх получасового кэша .ics) —
то, что рисует плашка: события дня (строкой `from`/`to` для подписи и ISO, чтобы страница сама решила, что уже
прошло), свободные окна от «сейчас» и во сколько начинается завтра. При сбое сети отдаётся последний удачный
ответ.

`health_review.sh` подставляет `schedule.py digest` в промпт секцией «Мой день»; если сети нет или календарь
сломался — заметка пишется как раньше, без расписания. `health/PROMPT.md` велит использовать его как рамку:
советовать прогулку в свободное окно и называть его время, не пересказывать календарь и не комментировать
события; ранний старт завтра — единственный повод сказать «ложись раньше» не вечером.

**Правка календаря** идёт по CalDAV (`end` / `add`), и правило не удаляется, а закрывается датой `UNTIL`:
удаление серии унесло бы и прошлое, а прошлое — единственная запись о том, что было. Две ловушки, обе стоили
крови: перед `VEVENT` в файле события лежит `VTIMEZONE` со своими семью `RRULE` (переводы часов), поэтому замена
«первого RRULE в файле» правит зону, а не событие — `set_rrule` работает строго внутри `VEVENT`; и название
не идентификатор — `end "ЛФК"` закрыл заодно только что созданную замену, теперь при нескольких совпадениях
нужен `--at HH:MM`. Сервер пересобирает `VTIMEZONE` сам, так что порча зоны до него не доезжает.

```
python3 scripts/schedule.py fetch [--force]   # обновить кэш
python3 scripts/schedule.py list [--days 7]   # ближайшие дни
python3 scripts/schedule.py digest            # то, что видит модель
python3 scripts/schedule.py today             # то, что видит плашка на главной (JSON)
python3 scripts/schedule.py free [--date ...] # свободные окна дня
python3 scripts/schedule.py calendars | rules  # коллекции / повторяющиеся правила
python3 scripts/schedule.py end "Психолог" [--at 09:00] [--date YYYY-MM-DD] [--dry-run]
python3 scripts/schedule.py rename "Старое имя" "Новое" [--at 19:00] [--once] [--dry-run]
python3 scripts/schedule.py add "ЛФК" --day FR --time 18:00 --minutes 60 [--once] [--dry-run]
python3 scripts/schedule.py add "ЛФК" --date 2026-09-25 --time 18:00   # начать с конкретного дня
```

**`--day` берёт ближайший такой день недели, включая сегодняшний** — поэтому у `add` есть `--date`: «сделай
по пятницам», сказанное в пятницу вечером, значит «со следующей», а правило с сегодняшним DTSTART положило бы
занятие в уже прошедший час и показало бы его в делах на сегодня (18.09.2026).

Состояние на 18.09.2026: закрыты датой «Психолог чт 09:00», «ЛФК ср 18:30» и «ЛФК ср 18:00» (18.09 перенесена
на пятницы: правило «ЛФК пт 18:00» с 25.09), удалены правила «Теннис сб 16:00» и «Тренировка в зале»
ср 09:00 / вс 10:00 — по просьбе пользователя, копии удалённого лежат в `data/calendar-deleted/` (данные машины,
в `.gitignore`). Действуют: «Работа пн–пт 10:00–18:00», «Французский вт/чт 19:00» (15.09 переименован из
«Французский Сережи» — это своё занятие, а не чужое; `rename` меняет SUMMARY, сохраняя UID, поэтому серия с её
историей и исключениями остаётся одним событием), «ЛФК пт 18:00». Разовые события заводятся так же, с `--once`:
19.09 «Теннис 11:00–13:00».

## Дом (`home.html`, `scripts/home.py`, `/api/home`)

Умный дом на сайте: скетч кухни (SVG, окно, полка, диван, картина с двумя бра), свечение ламп рисуется
по реальному состоянию — цвет из `color_temp`/`xy`, яркость в прозрачность.
За окном — часы, сезон и настоящая погода (`/api/weather`: восход/закат, что идёт): солнце и луна ходят по
стеклу, ночью звёзды и окна в домах, облака/дождь/снег по иконке прогноза, зимой снег на крышах, весной и осенью
птицы; небо своё на каждое время года. Клик по бра переключает группу `lamps`, клик по оранжевому диску Varmblixt — розетку `plug`; рядом
панель: тумблеры, яркость, тепло (кельвины → mired), сцены — через `serve.py`, а не через CLI `lamp` из скилла.
Растение у окна перерисовано 15.09.2026: листья закрашены бумагой и куст растёт прямо из горшка —
незакрашенные эллипсы на длинном голом стебле лежали поверх стекла и читались как петли из проволоки,
а не как крона. Правило на будущее: всё, что накладывается на окно, должно быть с заливкой.
Живой огонь (камин/свеча/факел) со страницы убран 14.09.2026 по просьбе пользователя; `effect.js` остался только для CLI. Сцены только белые, четыре: «Уютно» пользователя (43 %, 2300 K), «Чтение» и «Ночник» по рецептам Philips Hue
(температуры Hue, яркость × 0,76 — цифры Hue один к одному на Tuya светят ярче, чем задумано), «Кино» — 9 %,
бледно-голубой rgb 175/200/255 (пользователь хотел голубее, чем 6500 K, а это край белой шкалы лампы). Приглушённо/Отдых/Ярко/Собраться/Ко сну удалены 14.09.2026 по просьбе; цветные пресеты в духе WiZ (янтарь,
закат, кино, зелёный) убраны 14.09.2026 как некрасивые — не возвращать без просьбы. Колонка управления справа
задаёт высоту, а скетч дорисовывает стену вверх (`viewBox` пересчитывается по размеру карточки), чтобы ничего не резать.

**Датчики температуры и влажности** (17.09.2026): два eWeLink CK-TLSR8656 — `kitchen` = `0xa4c1381490f7ffff`,
`bedroom` = `0xa4c1381490f8ffff` (кухню пользователь опознал по влажности: она суше). Переименовать —
`bridge/request/device/rename`, поменять местами — только через временное имя, занятое имя брокер не отдаёт.
Это спящие end-device: `/get` их не будит, поэтому у обоих в Zigbee2MQTT выставлен `retain` — последний
отчёт лежит в брокере и приходит сразу после подписки. `home.py` их только слушает и отдаёт отдельным
ключом `sensors` в `/api/home` (`{title, temperature, humidity, battery, last_seen}`), а `home.html`
рисует табличку прямо на скетче — на стене под картиной, между двумя бра (x 470…830, ниже рамы: её низ
на y=210). **Рамку считает `fitClimate()` по `getBBox()`, а не глаз**: кегль разный на маке и на телефоне
(на телефоне весь скетч ужат до ширины экрана, 1000 единиц → ~370 px, поэтому там цифры втрое крупнее
в единицах SVG — иначе не прочесть), и рамка, подобранная на одном, на другом оказывается уже текста —
первая версия из неё и выехала. Комнаты стоят двумя колонками: вширь на той стене места больше, чем ввысь.
Текст внутри `.ink` надо возвращать к заливке
(`.ink text { stroke: none; fill: var(--ink) }`) — у группы обводка без заливки, иначе цифры не видны.
Новое устройство в сеть пускать так: `bridge/request/permit_join` `{"time": 254}`, а слушать события
`bridge/event` дольше 45 секунд одним соединением нельзя — клиент из `home.py` не шлёт PINGREQ, и брокер
рвёт связь по keepalive (окно сопряжения при этом живёт само по себе).

`scripts/home.py` — MQTT 3.1.1 клиент на голых сокетах (paho на mini нет): `state()` подписывается на
`zigbee2mqtt/{lamp,lamp2,plug}`, шлёт `/get` и ждёт до 2 с; `set_device()` пропускает только
`state/brightness/color_temp/color/transition`. **Каждая команда проверяется**: группа `lamps` — это Zigbee-broadcast
без подтверждения, лампа, пропустившая его, остаётся на старом свете (так «Приглушённо» однажды светило
как «Чтение»). Через `transition` + 1,5 с `_verify` спрашивает лампы и той, что показывает не то, шлёт тот же
payload адресно (unicast подтверждается); проверяется только последняя команда по цели (`_seq`), иначе быстрые
клики по сценам дрались бы друг с другом. В логе serve: `home verify lamp2: shows … — resending …`. Брокер: `MQTT_HOST`, иначе localhost
там, где есть `~/mqtt/effect.js` (mini), иначе LAN-имя mini → VPN 10.8.1.5. **На ноутбуке стоит свой
пустой mosquitto на localhost** — он отвечает, но устройств не знает, поэтому localhost на ноутбуке не пробуется.
**Вечерний свет идёт по закату** (`scripts/lamp_schedule.py`, 18.09.2026). До этого расписание висело в launchd
тремя жёсткими временами и жило вне репозитория (`~/mqtt` на mini): 19:00 уютно, 22:00 ночник, 23:00 выкл, плюс
`~/mqtt/lamp-catchup.sh` после перезагрузки. Беда в том, что 19:00 — это не вечер, а число: в декабре темнеет
в 16:00, в июне в 19:00 ещё день. Теперь «Уютно» привязано к закату (`LAMP_EVENING_OFFSET` = −30 мин, зажимается
между `LAMP_EVENING_EARLIEST` 15:30 и `LAMP_EVENING_LATEST` 21:00), а «Ночник» 22:00 и «выкл» 23:00 остались по
часам — они про сон, а не про солнце. Закат считает `scripts/sun.py` (NOAA, точность ~минута) **без сети**:
погодный API знает закат, но вечером, когда лёг VPN, лампы обязаны зажечься всё равно.

Один агент `cc.bodywithoutorgans.lampschedule` (`deploy/install-lamp-schedule.sh`, раз в 5 минут) вместо шести
прежних; старые уносятся в `~/Library/LaunchAgents/disabled-by-lamp-schedule`. Свет переключается **только на
границе фазы** — внутри фазы лампы не трогаются, поэтому выключенный вручную в девять вечера свет так и
останется выключенным до ночника. Прежний catchup заменён проверкой `kern.boottime`: если mini поднялся уже
после того, как фаза была применена, она применяется заново (после сбоя питания лампы просыпаются сами и не в
той сцене). Утро и день — фаза `day`: свет не трогается вовсе, в том числе после перезагрузки. Строку расписания
на странице «Дом» рисует `lamp_schedule.describe()` («закат 18:41 · 18:11 Уютно · 22:00 Ночник · 23:00 выключить»),
`home.SCHEDULE` остался запасной надписью. Фаза запоминается в `data/lamp-phase.json` — данные машины,
в `.gitignore` и в исключениях `deploy.sh`.

**История датчиков** (`scripts/sensors.py`, 18.09.2026). У самих датчиков истории нет: их последний отчёт лежит
в брокере retained, и всё. Раз в 10 минут launchd `cc.bodywithoutorgans.sensors` (`deploy/install-sensors.sh`)
снимает показания в таблицу `room` в `health.db` — в ту же базу, где сон и пульс, под тот же ночной бэкап.
Ключ строки — не время съёмки, а `last_seen` датчика: спящее устройство отчитывается, когда захочет, и один и
тот же отчёт мы видим много раз подряд, `INSERT OR IGNORE` оставляет его одной строкой. `/api/home` отдаёт
разброс за сутки отдельным ключом `day`, и на табличке в скетче появляется третья строка «за сутки 21,4–24,2°» —
но только если `fitClimate()` видит, что рамка от неё не дорастёт до спинки дивана (`CLIMATE.floor` = 320;
на телефоне кегль втрое крупнее, и то, что помещается на маке, там упёрлось бы в мебель).

Ради чего всё это — `sensors.py night`: у сна есть стадии, у спальни градусы, и через месяц можно спросить,
падает ли глубокий сон в тёплые ночи. Пока ночей с показаниями меньше `MIN_NIGHTS` = 14, сравнение молчит —
на пяти ночах это было бы не выводом, а совпадением. В промпт заметки о здоровье температура пока **не**
уходит: сначала данные, потом советы.

```
python3 scripts/home.py state                       # что видит страница
python3 scripts/home.py set lamps '{"state":"ON","brightness":120}'
python3 scripts/home.py scene cozy | effect fire | effect off
python3 scripts/lamp_schedule.py show               # во сколько сегодня что (ничего не трогает)
python3 scripts/lamp_schedule.py run [--force]      # применить фазу (это и делает launchd)
python3 scripts/sun.py [YYYY-MM-DD]                 # восход и закат
python3 scripts/sensors.py log | list | today       # снять показания / что записано / сутки
python3 scripts/sensors.py night [--days 30]        # ночь за ночью: сон против спальни
ssh mini tail -5 movies/logs/lamp-schedule.log
ssh mini tail -5 movies/logs/sensors.log
```

## Mixes (`mixes.html`, `mixes.json`)

Morning-mix player: random mix from the collection, "next" button, sources SoundCloud / YouTube /
Mixcloud / NTS (NTS episodes resolve to their SoundCloud or Mixcloud audio). Playback uses the
official embed widgets, so it needs internet; the page controls them via their JS APIs.
The user adds single mixes by pasting a URL into the site (POST `/api/mix`) or by asking Claude Code:

```
python3 scripts/mixes.py add <url>                                  # soundcloud / youtube / mixcloud / nts.live
python3 scripts/mixes.py import-soundcloud <user> [--min-minutes 20] [--dry-run]   # user's public likes, long tracks only
python3 scripts/mixes.py import-youtube [LL] [--pick 1,4,videoId] [--tag calm]      # my YouTube likes
python3 scripts/mixes.py remove <id|url|title>
python3 scripts/mixes.py list
```

- Needs `yt-dlp` (brew) for YouTube/Mixcloud metadata; SoundCloud uses api-v2 with the client_id from yt-dlp's cache.
- `import-youtube` reads the private «Liked videos» playlist (`LL`) through the cookies of a logged-in
  browser (`--browser firefox`, works on the laptop; the mini has no browser and no YouTube without VPN).
  It never adds in bulk on purpose: the same likes hold lectures, let's plays and Мэддисон, so it prints
  the long unknown videos numbered and adds only what `--pick` names (numbers or video ids). Tag the
  ambient ones `--tag calm` — see below.
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
- **До 9 утра (`MORNING_UNTIL` в `index.html` и `mixes.html`) сайт показывает только спокойное**: в это
  время человек читает, а не танцует. Спокойный микс в коллекции помечен тегом `calm`, спокойный совет —
  полем `mood: "calm"`. До девяти «Микс на сегодня» на главной превращается в «Под утреннее чтение» и
  берётся из `calm`-части коллекции, случайный микс на странице миксов (и кнопка «дальше») тоже, а советы
  Claude сортируются спокойными вперёд. Если спокойного нет вовсе — всё как обычно, без пустых блоков.

### Daily advice from Claude (`mix_recs.json`, `mixes/PROMPT.md`, `scripts/mix_recs.py`, `scripts/mix_recs.sh`)

Once a day the mini asks Claude Code for new mixes. `mix_recs.py digest` = collection + what was actually
played (position/playedAt) + verdicts on earlier advice; `mixes/PROMPT.md` asks for 9 candidates as JSON
(`search`, `artist`, `title`, `mood`, `why`); `mix_recs.py apply` resolves them through SoundCloud search
(`/search/tracks`, ≥20 min, the asked-for artist must really be in the track, nothing already known) and
keeps the first 3 rhythmic + 2 calm in `mix_recs.json` (`items` + `history`) — two quotas (`--keep`,
`--keep-calm`), because with one list the rhythmic ones eat the whole batch and mornings stay empty.
The calm half is a separate brief in the prompt (ambient, drone, modern classical, quiet jazz, game
soundtracks — no dance pulse), and the digest shows the model what `calm` mixes already are in the
collection. «Нравится» on a calm suggestion adds it with tags `claude-rec` + `calm`. The mixes page shows them as «Советует Claude»
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

## Советы по фильмам, книгам и лекциям (`taste_recs.json`, `taste.js`, `scripts/taste_recs.py`, `scripts/taste_recs.sh`)

Тот же механизм, что у миксов, но для трёх каталогов сразу. `taste_recs.py digest film|book` собирает
контекст вкуса: фильмы — все оценённые с режиссёром, жанрами и моими заметками плюс список «посмотреть»;
книги — что прочитано, что **брошено** (главный отрицательный сигнал) и что в планах. Оценок у книг нет
(`rating` пустой у всех, теги — цветные квадраты из Obsidian), поэтому там вкус читается по составу;
если оценки появятся через страницу, дайджест подхватит их сам.

Лекции (2026-09-15) — третий вид, `digest lecture`: что дослушано и что слушается (у лекций нет
оценок, а «не слушал» ничего не значит — в каталоге лежат все 400 роликов двух каналов целиком,
руками их никто не выбирал), что в планах, темы серий и **запрет на сами эти каналы**: они уже
выкачаны, советовать оттуда нечего.

`films/PROMPT.md`, `books/PROMPT.md` и `lectures/PROMPT.md` просят 6 кандидатов в виде JSON
(`title`, `year`/`author`, `why`; у лекций ещё `search`), `taste_recs.py apply` проверяет каждого:
фильм — через OMDb (`movies.py resolve` + `metadata`), книгу — через Google Books / Open Library
(`books.py lookup`), лекцию — через поиск YouTube (`yt-dlp ytsearch8:`, ≥25 минут, названный лектор
должен реально быть в канале или названии, не live, не с уже выкачанных каналов). Модель не даёт ссылок
вовсе — ровно как в `reads.py`: ссылку подставляет поиск, поэтому выдуманного URL быть не может.
Выдуманное название и то, что уже в каталоге, в истории или в советах, отсеивается; первые
3 подтверждённых ложатся в `taste_recs.json`.

Картинки советов, скачанные на mini, раньше стирал каждый деплой (`rsync --delete` с ноутбука, где их не было);
с 2026-09-13 `deploy.sh` сначала забирает `posters/` и `covers/` с mini. Совет без картинки или непроверенный
(каталоги молчали) чинится на месте: `python3 scripts/taste_recs.py covers [film|book]` — на mini, это production.

Обратная связь — весь смысл: «Хочу» переносит фильм в `movies.json` со статусом `to-watch` (постер
скачивается локально, причина совета уезжает в `note`), книгу — в `books.json` со статусом `to-read`
(тег `claude-rec`), лекцию — в `lectures.json` со статусом `queued` в канал «Советы Claude»
(`L.RECS_CHANNEL`), откуда её сразу подхватывают и очередь на странице, и `preload` телефона —
mini качает звук заранее; «Не то» — отказ. И то, и другое ложится в `history` и попадает в следующий
промпт. Превью лекции — ссылка на `i.ytimg.com`, локально ничего не лежит, поэтому `covers` лекции
пропускает.

**У лекций есть третий ответ — «Уже слушал»** (`KIND_VERDICTS`, просьба пользователя 15.09.2026;
у фильмов и книг его нет, API отвечает `bad verdict`). Это не «не то»: направление верное, просто
лекция уже знакома, — она уезжает в `lectures.json` сразу `listened` и дальше работает как вкус,
на который опирается следующий подбор, а в промпте помечена «УЖЕ СЛУШАЛ» и засчитывается попаданием
наравне с «ВЗЯЛ». Набор кнопок задаёт страница (`acts` в `Taste.mount`), `taste.js` общий.

**Средневековье со стороны не предлагать** (`lectures/PROMPT.md`, 15.09.2026): 300 часов Макарова
про средневековый быт эту территорию закрывают, первый же совет про культуру Средневековья был
отвергнут. Остальная история — Новое время, XX век, античность, неевропейские регионы,
экономическая история — в силе.

```
python3 scripts/taste_recs.py digest film|book|lecture   # что видит модель
python3 scripts/taste_recs.py list [film|book|lecture]   # текущие советы
sh scripts/taste_recs.sh [film|book|lecture|all] [--force]   # спросить сейчас (на mini: ssh mini 'cd movies && sh scripts/taste_recs.sh all --force')
python3 scripts/taste_recs.py verdict film <tt…> liked|dismissed
ssh mini tail -20 movies/logs/taste-recs.log
```

launchd на mini: `cc.bodywithoutorgans.tasterecs`, ежедневно в 07:40 (`deploy/install-taste-recs.sh`).
API: `GET /api/recs/<film|book|lecture>`, `PATCH /api/rec/<kind>/<id>` `{verdict}`,
`POST /api/recs/<kind>/refresh` (кнопка «Обновить» в блоке «Советует Claude» на всех трёх страницах,
общий код — `taste.js`).

### Главная: слева день, справа полка

Два переустройства подряд, 18.09.2026, оба по просьбе пользователя.

Сначала — **вид**: «много блоков, какой-то хаос». Десять белых плашек с рамками, тенями и чёрными кнопками
поверх фотографии во всю страницу и были хаосом. Теперь заметка начинается волосяной линейкой сверху и стоит
прямо на бумаге: ни фона, ни рамки, ни тени. Фотография осталась, но стала обложкой — полоса 380 px вверху,
растворяющаяся в бумаге. Кнопки стали подписями киноварью прописными. Кнопка не прижимается к низу
(`margin-top: auto` убран), карточка не растягивается на высоту строки (`align-self: start`) — из-за этого
соседние заметки расходились рваными столбцами.

Потом — **состав**: «погода, well-being и т. п. на главной, а миксы, лекции и т. д. — сбоку». Страница делится
не по весу, а по смыслу (`.page` = две колонки):

- **слева день** — «Дела на сегодня», погода с часами, **Well-being** (заметка целиком плюс сон / пульс покоя /
  HRV / шаги / тренировка цифрами антиквой), «Чем занять окно». Порядок именно такой: сначала что за день,
  потом как я, потом план на вечер;
- **справа «Полка»** (`#shelf`, липкая на десктопе) — что послушать и почитать: «Микс на сегодня», «Совет от
  Claude», «Почитать» (с кнопками «Прочту» / «Мимо» — вердикты те же, что на странице «Читать»), «Читаю»,
  «Французский». Строки компактные: рубрика, заголовок антиквой в две строки, подпись курсивом.

**Лекции с главной убраны совсем** (18.09.2026): слушаются они только с телефона, где у них свой нативный таб,
а на сайте — свой раздел в верхней панели, так что карточка на главной была третьим входом в то же самое.
`lectures.json` главная больше не загружает вовсе (из `rec.js` остался только `fmtDur`). В «Чем занять окно»
лекция по-прежнему может появиться — но это предложение на вечер, а не каталог.

На ширине до 1080 px колонки складываются в одну и **день остаётся сверху**: утром на телефоне (и в приложении
BoW, где главная — первый таб) нужен именно он, а не полка. Вёрстка живёт в самой странице, как у всех
остальных: в `theme.css` только палитра и типографика.

**«Чем занять окно»** (`scripts/fit.py`, `GET /api/fit`, кэш 5 минут, 18.09.2026) — вторая полоса, сразу под
делами дня, потому что она про тот же день. Это то, ради чего сайт вообще знает разом календарь, погоду, закат
и длину каждой лекции, микса и статьи вместе с позицией в них: вопрос не «что посмотреть», а «до 23:00 четыре
часа, дождя нет — что влезет целиком». Ничего не выдумывается: берётся начатое и то, что уже в планах, и
отбирается по длине (`best()`: сперва начатое из влезающего, потом самое длинное из влезающего; если не влезает
ничего — самое короткое с честной подписью «не влезет целиком»). Один кандидат на вид (лекция, микс, статья,
французский), всего три плитки.

Прогулка — отдельная плитка и отдельные правила: окно от `WALK_MIN` = 40 мин, светлого времени внутри окна не
меньше `WALK_LIGHT` = 25 мин (закат из `sun.py`), осадки внутри окна меньше `WET` = 50 % и не мороз. Погода
смотрится **в часах самого окна**, а не «на сегодня»: дождь в семь утра прогулке в восемь вечера не помеха.
Если шагов за день уже `STEPS_ENOUGH` = 8000, прогулку не предлагают. Ссылки у неё нет — это совет выйти из
дома, а не страница сайта. Нет свободного окна — карточки нет вовсе.

```
python3 scripts/fit.py            # то, что видит карточка (JSON)
python3 scripts/fit.py text       # то же словами
```

«Дела на сегодня» — узкая полоса над погодой (`/api/schedule`): события дня «плашками», прошедшие приглушены,
идущее сейчас подсвечено акцентом и показывает, когда кончится, помеченные «свободен» — пунктиром; справа в
подписи свободные окна дня, а когда на сегодня ничего не осталось — во сколько начинается завтра.
**Если кроме «Работы» на сегодня ничего нет, плашки нет вовсе** (`ROUTINE` в `index.html`, просьба пользователя
14.09.2026): рабочий блок 10:00–18:00 стоит каждый будний день, и полоса с одним этим словом каждое утро —
не дела, а шум. Пустой день и недоступный календарь — тоже без плашки.

Лекции — **одна плашка**, а не по карточке на канал: показывается та лекция, которую слушаю сейчас (среди
`listening` — с самым свежим `touchedAt`), иначе первая из планов, иначе рекомендация; в подписи — канал,
позиция и сколько ещё в планах. Двух каналов на главной больше нет: это были две полупустые карточки об одном
и том же.

Карточки «Вечер» (фильм из «посмотреть» + прогулка) больше нет — удалена по просьбе пользователя вместе с
функцией `eveningCard` и загрузкой `movies.json` на главной. Совет про прогулку никуда не делся: его даёт
заметка о здоровье, которая теперь знает и расписание, и свободные окна дня.

## Статьи (`reads.html`, `reads.json`, `reads/PROMPT.md`, `scripts/reads.py`, `scripts/reads.sh`)

Единственная рекомендация, которая смотрит на всю картину сразу: `reads.py` строит дайджест вкуса
по четырём каталогам разом — фильмы с оценками и заметками, прочитанные и брошенные книги, лекции
в работе, состав музыки и что было забрано из советов по миксам, — и добавляет к нему пронумерованный
список свежих статей, который сам скачал из RSS.

Модель НЕ называет ссылки: она возвращает `[{"n": 12, "why": "…"}]`, то есть номер из списка кандидатов.
Так сделано потому, что правдоподобные несуществующие URL — её любимая ошибка (ровно как выдуманные
фильмы, пока их не начали проверять в OMDb). Ссылка всегда живая, потому что пришла из ленты.

Ленты (`SOURCES` в `reads.py`, все проверены с mini через VPN): LessWrong (кураторская), Astral Codex Ten,
Aeon, Psyche, Noema, London Review of Books, The Paris Review, Harper's, The New Yorker, Quanta, Nautilus.
`PROMPT.md` требует, чтобы минимум две статьи из пяти открывали территорию, которой в каталогах нет
вовсе, — запрос был именно на новое, а не на подтверждение вкуса.

Состояние в `reads.json`: `items` (текущие советы), `saved` (список чтения), `history` (вердикты),
`cache` (кандидаты из лент, странице не отдаётся). Кнопки: «Прочту» → `saved`, «Мимо» → история,
«Прочитал» в списке чтения → история. Всё это идёт в следующий промпт.

```
python3 scripts/reads.py fetch | digest | list
sh scripts/reads.sh [--force]                  # на mini: ssh mini 'cd movies && sh scripts/reads.sh --force'
python3 scripts/reads.py verdict <id> saved|dismissed|read
python3 scripts/reads.py images            # дозаполнить превью (на mini — это production)
ssh mini tail -20 movies/logs/reads.log
```

Превью: у лент картинок почти нет, поэтому `apply` берёт `og:image` со страницы статьи, ужимает через `sips`
до 1200 px и кладёт в `reads/img/<id>.jpg` (`image` в записи). Это данные машины, как `dishes/`: в `.gitignore`
и в исключениях `deploy.sh`; картинка удаляется вместе с вердиктом «мимо»/«прочитал» и когда совет уезжает
в историю без ответа. `why` по просьбе пользователя (2026-09-14) — одна фраза до 20 слов с одной зацепкой,
а не перечисление всего прочитанного по теме.

launchd на mini: `cc.bodywithoutorgans.reads`, ежедневно в 07:50 (`deploy/install-reads.sh`).
API: `GET /api/reads`, `PATCH /api/read/<id>` `{verdict}`, `POST /api/reads/refresh`.
На главной — карточка «Почитать» со ссылкой прямо в текст.

## Французский (`french.html`, `french.json`, `french/PROMPT.md`, `french/LESSON.md`, `french/DRILL.md`, `scripts/french.py`, `scripts/french.sh`)

Занятия между уроками с преподавателем (они по вторникам и четвергам в 19:00, видны в календаре):
короткие видео и статьи на раз, к ним разбор — слова и задания, — плюс свой урок на тему и перевод,
а из ответов растёт колода с интервальным повторением. Уровень задаётся на странице
(`french.py level B1`, по умолчанию B1) и уходит во все промпты.

**Видов занятий четыре, и делятся они пополам** (19.09.2026, просьба пользователя «хочется
разнообразнее»). `video` и `article` приезжают из лент и с YouTube — настоящий материал, выбранный
НОМЕРОМ из списка кандидатов (`FEED_KINDS`). `lesson` и `translation` (`DRILL_KINDS`) модель пишет
с нуля, и это не нарушение правила «ничего не выдумывай»: выдумать нельзя ссылку, а объяснение
passé composé выдумывать и не надо — оно и есть содержание. Тема при этом не на усмотрение модели:
склад урока крутится по кругу (`DRILL_FLAVOURS`: грамматика → лексическое поле → живые выражения →
глагол с предлогами, `drillCursor`, ровно как `searchCursor` у поиска видео), содержание бьёт в мои
ошибки, примеры берутся из моего вкуса, а фразы на перевод — из слов, которые уже лежат в колоде
(повторение в контексте вместо ещё одной порции незнакомого). Урок — это объяснение частями
(`sections`: правило, случаи, частая ошибка, у каждой части примеры с переводом), перевод — сквозная
ситуация из 6–8 фраз.

**Типов заданий шесть** (`QUIZ_TYPES`), и каждый проверяется сервером по сохранённому тесту:
`choice` — по индексу, `match` (составь пары) — по перестановке, `gap`, `translate`, `order`
(собери фразу из перемешанных слов) и `listen` — по тексту через `norm()`. Исключение одно:
у открытого перевода (`SELF_TYPES`) судья я — правильных переводов у фразы больше, чем влезает
в `accept`, поэтому кнопка «Всё равно верно» шлёт `self: true`, и сервер её честно засчитывает.
Только у перевода: в остальных типах ответ один, и верить клиенту не за что.
**`norm()` теперь пропускает кириллицу** (и в `french.py`, и в странице): без неё `norm("привет")`
давал пустую строку, любые два русских ответа сходились, а задание с русским ответом засчитывалось
всегда. Правила пишутся в обоих файлах одинаково — страница показывает результат сразу, сервер
пересчитывает его сам.

**Карточки спрашивают по-разному** (`pickMode` в странице): узнавание (фр → рус) в первых коробках,
дальше вспоминание (рус → фр) и набор руками, плюс два вида, которых раньше не было, — пропуск
в примере из разбора (`cloze`, слово вырезается из настоящей фразы) и на слух. Режим выбирается
случайно из того, что коробке по силам: одна и та же карточка одним и тем же жестом перестаёт учить.

**Звук — системный синтезатор речи** (`speechSynthesis`, голос `fr-*`). Отдельного файла и сети это
не требует, но французский голос есть не в каждой системе (в Firefox на маке его надо доустановить),
поэтому всё звуковое сначала спрашивает `canSpeak()`: нет голоса — нет ни кнопок «♪», ни заданий
на слух, и страница работает ровно как раньше. Голоса приезжают асинхронно, так что
`voiceschanged` перерисовывает страницу, если французский появился уже после отрисовки.

Приложению BoW ничего не нужно: «Французский» — это тот же вебвью с этой страницей, и синтезатор
у WKWebView свой, системный.

**Два прохода, и оба нужны.** Первый: скрипт сам скачивает кандидатов (десять французских лент +
поиск YouTube), модель выбирает три **номером** из списка — ссылок она не пишет вовсе, ровно как
в `reads.py`, потому что правдоподобный несуществующий URL это её любимая ошибка. Второй: скрипт
качает настоящий текст выбранного — французские субтитры ролика (`yt-dlp --write-auto-subs
--sub-langs "fr.*"`, VTT → сплошной текст) или абзацы `<p>` статьи — и модель собирает разбор
**по этому тексту**: 6–8 слов с примерами из него и 5–6 вопросов. Без второго прохода тест был бы
про название ролика, а не про ролик. Побочный, но важный эффект: нет французских субтитров — нет
и материала, и это единственная надёжная проверка, что «французское» видео действительно французское.

**Интересы — полный вкус, а не выжимка** (15.09.2026). `interests()` отдаёт то же самое, что видят
советы по статьям (`reads.taste()`): фильмы с оценками и заметками, фильмы, которые не зашли, все
прочитанные книги, брошенные (сигнал против), планы, лекции и музыка. Первая версия давала семь
строк выжимки — но язык учится на том, что интересно и без языка, и выбирать материал надо по всему
вкусу: «La Nueve, ces Espagnols qui ont libéré Paris» нашлась именно потому, что в лекциях лежит
гражданская война в Испании.

Кандидаты: `SOURCES` — 1jour1actu, Français Authentique, RFI, France Info, Slate, Courrier
International, Le Monde, The Conversation, Philosophie Magazine, France Culture (у каждого в
дайджесте помечена сложность языка: модель не видит уровня текста иначе). `SEARCHES` — 22 запроса
к YouTube: половина каналы для изучающих, половина настоящие французские каналы по моим темам
(история, философия, наука, экономика, кино) — язык учится на том, что и без языка интересно.
За раз крутятся `SEARCH_ROTATE` = 6 запросов по кругу (`searchCursor`), иначе каждый день
предлагались бы одни и те же ролики. Видео берётся длиной 2,5–20 минут: час французского
«когда-нибудь потом» не смотрится никогда (именно поэтому innerFrench с его получасовыми
выпусками в выдачу не проходит).

**Память об ошибках** — то, ради чего всё это. У каждого вопроса модель ставит `tag`
(«passé composé», «род существительных», «лексика: работа»); ответы (и по тесту, и по карточкам)
ложатся в `answers`, а `weak_tags()` складывает их в «где я сыплюсь». Это уходит и в первый промпт
(«материал, который даёт потренировать именно это, ценнее просто интересного»), и во второй
(«хотя бы один вопрос целься в мою слабую тему»). Ответы на «впиши слово» сервер пересчитывает
сам по сохранённому тесту — клиенту верят только на слово «я ответил вот это». Регистр и диакритика
при сравнении не учитываются (`norm()` есть и в `french.py`, и в странице — учу язык, а не раскладку).

**Карточки** — коробки Лейтнера (`BOX_DAYS` = 0/1/3/7/16/35 дней): верный ответ поднимает коробку,
ошибка возвращает в первую. До третьей коробки спрашивается узнавание (фр → рус), дальше
вспоминание (рус → фр); варианты набираются из соседних карточек. Слова уезжают в колоду, когда
материал закрыт кнопкой «Готово», а не при показе.

**Новое ищется, когда кончилось старое.** `PATCH /api/french/<id>` видит, что материалов не
осталось, и сам запускает `french.sh --force` (`start_job`) — «закончил предыдущее» и есть самый
честный повод идти за новым; плюс launchd раз в день. Кончились одни упражнения, а видео и статьи
на месте — хватает третьего прохода: `french.sh --drills` (в `finish` для этого два числа, `left`
по материалам из лент и `drills`). Календарь (`schedule.py digest`) идёт в промпт
не ради темы, а ради размера: в плотный день материал должен быть короче; на странице из него же
строка «Занятие сегодня в 19:00» и свободное окно.

```
python3 scripts/french.py fetch | digest | lesson-digest    # кандидаты / что видит модель в 1-м и 2-м проходе
python3 scripts/french.py list | cards | stats
python3 scripts/french.py level B2
python3 scripts/french.py drill-digest                       # что видит модель в 3-м проходе
sh scripts/french.sh [--force] [--lessons] [--drills]
                                             # --lessons: досбор разборов, без нового подбора
                                             # --drills: только новый урок и перевод
                                             # (на mini: ssh mini 'cd movies && sh scripts/french.sh --force')
ssh mini tail -20 movies/logs/french.log
```

launchd на mini: `cc.bodywithoutorgans.french`, ежедневно в 08:10 (`deploy/install-french.sh`).
API: `GET /api/french`, `PATCH /api/french/<id>` `{verdict: done|dismissed, answers:[{i,given}]}`,
`POST /api/french/cards` `{results:[{id,correct}]}`, `POST /api/french/level`, `POST /api/french/refresh`.
`french.json` правится через сайт (ответы, вердикты, уровень), поэтому он в списке `DATA` в `deploy.sh`;
превью статей `french/img/` — данные машины, в `.gitignore` и в исключениях `deploy.sh`, как `reads/img/`.
Отсюда же и грабли: картинку, скачанную ноутбуком, mini не увидит — `python3 scripts/french.py images`
**на mini** дозаполняет превью текущих материалов (как `reads.py images`). На странице заглушка с именем
источника лежит под картинкой всегда, так что пропавший файл даёт подпись, а не битую иконку.

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
python3 scripts/lectures.py import-likes [--pick 3,5,…]  # мои лайки на YouTube → лекции «прослушано»
python3 scripts/lectures.py set "<title|id>" --status listened|listening|new [--position SEC]
python3 scripts/lectures.py audio "<title|id>" ...     # download m4a into audio/ (gitignored, per machine)
python3 scripts/lectures.py list [--status ...]
python3 scripts/lectures.py add-channel <url>
```

- When the user says "послушал лекцию про X" → find it with `list`/grep and `set --status listened`.
  "Хочу послушать X" → `set --status queued` (queue order = time of queuing, shown on the home page).
- `sync` re-fetches both channels; YouTube playlists take minutes, use `--no-playlists` for a quick refresh.
- Audio files live only on the machine that runs the server (the Mac mini in production): download there.
- **Телефон держит три лекции офлайн, по одной на канал** (18.09.2026, просьба пользователя): `GET
  /api/lectures/preload` отдаёт `lectures.py preload` — сначала та, что слушается (последняя тронутая,
  `touchedAt` ставится при каждом PATCH позиции), потом по одной следующей из каждого канала, всего
  `PRELOAD_COUNT` = 3. Следующая в канале (`channel_next`) — начатая, иначе первая из планов, иначе совет
  `recommend` (порт `rec.js` на Python, им же страница считает «рекомендую»); канал, который ещё ни разу
  не слушали, не предлагает ничего — качать наугад из трёхсот новых нечего. Раньше список был «начатая плюс
  очередь подряд», и семь египетских лекций Bushwacker выдавливали Макарова: в дороге хочется выбрать, а не
  слушать одну серию. Каналы идут в порядке планов (где очередь поставлена раньше, тот и первый). Сам запрос запускает
  `start_audio_job` для тех, у кого нет звука, так что mini качает следующую лекцию заранее (yt-dlp с mini
  до YouTube дотягивается). Приложение скачивает файлы фоновой URLSession, шлёт позицию каждые 15 с и на паузе,
  по концу ставит `listened`, удаляет файл и берёт следующую. Список обновляется при открытии, по BGTask и после
  каждой прослушанной. Позиция на сервере обрезается по длительности лекции (была 4:08 у лекции на 2:51).
  Запросы к `/audio/` mini логирует строкой `audio <file> → iphone [Range]` (Range = докачка после обрыва).
- **Два канала без канала.** Кроме двух отслеживаемых каналов в `lectures.json` есть синтетические,
  с `"type": "manual"` (их пропускает `sync`): `likes` — «Лайки на YouTube», разовый импорт
  `lectures.py import-likes` (лайки — не каталог лекций, там же музыка, лет'с плеи и Мэддисон,
  поэтому команда печатает длинные незнакомые видео с номерами и добавляет только то, что названо
  в `--pick`; всё добавленное сразу `listened` — это прошлое, а не планы, и нужно оно затем, чтобы
  советам было на чём стоять), и `claude` — принятые советы (см. ниже). Лайк на ролике канала, за
  которым я и так слежу, уезжает в этот канал, а не в «лайки», иначе он выпал бы из серий и из фильтра.
  У такой лекции есть поле `author` (настоящий автор ролика) — карточка и плеер показывают его вместо
  имени канала. 15.09.2026 так импортировано 37 лекций: Макаров, Bushwacker с YouTube, Карпаты про LLM,
  SHIZ про дзета-функцию, Фуко, Жижек, Смулянский, Галковский, Redroom, Пятигорский, Хомский.
- На главной лекций нет (18.09.2026, просьба пользователя): слушаются они с телефона, где у них свой таб,
  а на сайте — свой раздел в панели. Каналы и их `label` в `lectures.json` нужны странице лекций и `preload`,
  главная их не читает.

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
**Сайт поднимается сам.** Демон VPN переживает падение процесса, но настоящая авария (11.09, 15.09.2026)
выглядит иначе: `amneziawg-go` жив, `utun11` держит `0/1`, а WG-сессия протухла — рукопожатия нет, `rx` стоит,
наружу с mini не выходит ничего, cloudflared крутит `dial tcp …:7844: i/o timeout`, сайт отдаёт 530 (Cloudflare 1033).
`scripts/vpn_watchdog.sh` (launchd `cc.bodywithoutorgans.vpnwatchdog`, раз в 2 минуты, `deploy/install-vpn-watchdog.sh`)
поэтому проверяет не процессы, а результат: `GET /healthz` снаружи, одним запросом через VPN → край Cloudflare →
тоннель → `serve.py`. Две неудачи подряд — и лечение лестницей: serve → VPN (+ сразу cloudflared, он бы досиживал
бэкофф до 64 с) → cloudflared. Подробности и ручные команды — `deploy/README.md`.
```
ssh mini tail -20 movies/logs/vpn-watchdog.log      # только аварии и починки, молчит когда всё хорошо
ssh mini 'sudo -n /usr/local/sbin/vpn-status.sh'    # рукопожатие и rx/tx руками
```

The mini reaches SoundCloud/Cloudflare/Open-Meteo directly, and since the VPN LaunchDaemon became a full tunnel
it reaches YouTube too (проверено 15.09.2026: `yt-dlp ytsearch` и `curl youtube.com` → 200) — на этом стоят
и `start_audio_job` (mini сам качает звук лекций из `preload`), и поиск советов по лекциям. Скачивать звук
на ноутбуке (`scripts/lectures.py audio <id>`) и толкать `audio/` на mini через `scripts/sync.sh` по-прежнему
можно, но это уже запасной путь, а не единственный.

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

## Чеки из налоговой (`scripts/lkdr.py`, 18.09.2026)

Второй источник чеков и гораздо более полный, чем почта. Почтовый конвейер (`pantrylib`) видит только то,
что ОФД согласился прислать письмом: 1151 чек, из них с товарами 597, и почти всё — ВкусВилл. «Мои чеки
онлайн» (lkdr.nalog.ru) хранит **каждый** чек, где на кассе назвали телефон: **1676 чеков с 2018-07-07**,
из них 1570 с товарными строками (всего 5358 строк). И там не еда, а жизнь: ВкусВилл 423, Яндекс.Еда 168,
Яндекс.Такси 224, Авито 112, МТС 96, Озон 86, метро 69, Деливери 61, самокаты 34, Яндекс Плюс, Ясно.Лайв,
Сбермаркет. Провал 2023 года (15 чеков) — это не аскеза, а то, с какого момента телефон стал называться
на кассе регулярно.

**Пути API берутся из бандла самого сайта, а не из чужих репозиториев** — те устарели. Сверено
18.09.2026 по `static/js/main.*.chunk.js`: вход переехал в v2 и называется `phone`, подтверждение и
обновление токена остались в v1. Старые `v1/auth/challenge/sms/*` отвечают 404.

```
POST /api/v2/auth/challenge/phone/start   {phone, captchaToken, deviceInfo}
POST /api/v1/auth/challenge/phone/verify  {challengeToken, phone, code, deviceInfo}
POST /api/v1/auth/token                   {refreshToken, deviceInfo}
POST /api/v1/receipt                      {limit, offset, dateFrom, dateTo, orderBy, inn, kktOwner}
POST /api/v1/receipt/fiscal_data          {key}
```

**Вход невозможно сделать безголовым: на нём Яндекс-капча** (`empty.captcha`), и решать её незачем и
нельзя. Поэтому капчу проходит человек — один раз, в браузере, — а скрипт перенимает ключ и дальше живёт
сам: `refreshToken` **бессрочный** (в его JWT нет `exp`, внутри `"expiration": null`), а часовой токен
обновляется по нему без всякой капчи — ровно это делает сам сайт в перехватчике 401. Перенос ключа: в
консоли на lkdr.nalog.ru (войдя с «запомнить меня», иначе ключ в `sessionStorage`)

```js
copy(JSON.stringify({deviceId: localStorage.getItem('sourceDeviceId'),
  refreshToken: localStorage.getItem('refresh.token') || sessionStorage.getItem('refresh.token')}))
```

и `pbpaste | python3 scripts/lkdr.py adopt` — через терминал и историю шелла секрет не проходит.
Ключи сайта в localStorage: `sourceDeviceId`, `auth.token`, `refresh.token`, `auth.token.store`.

```
pbpaste | python3 scripts/lkdr.py adopt   # перенести вход из браузера
python3 scripts/lkdr.py sync [--full]     # новые чеки + позиции (обычный проход встаёт на первом знакомом)
python3 scripts/lkdr.py stats             # период, месяцы, продавцы
```

Что надо знать про сами данные, прежде чем что-то по ним считать:

- **Товарные строки живут около пяти лет.** У 106 чеков 2018–2020 годов их уже нет: ФНС отвечает «сервис
  получения чеков временно недоступен» — всегда, а не временно. После `MAX_TRIES` = 2 попыток такой чек
  больше не запрашивается, иначе каждая синхронизация тратила бы сотню запросов впустую (`--retry-errors`,
  если понадобится).
- **`totalSum` — не потраченные деньги.** Чек о передаче товара печатается на всю сумму, но оплачен
  зачётом ранее внесённого аванса (`prepaidSum` > 0, `paymentType: 4`) — денег по нему не двигается.
  Таких чеков 153 из 1676, на 36,8 млн ₽: считать по `totalSum` значит посчитать почти половину трат
  дважды. Тратами считается `cashTotalSum + ecashTotalSum` (`paid()` в `lkdr.py`); чек-зачёт узнаётся
  по `offset()`. Двойной счёт не только у застройщика — так же приходят Озон (44 чека), Авито (40),
  ВкусВилл с доставкой (21), СДЭК: платишь онлайн, а чек на товар печатают при передаче.
  **То же самое почти наверняка есть и в почтовом конвейере `pantrylib`** — там расход считается по
  покупкам, и пара «предоплата + передача» удваивает товар; при следующей правке еды это надо проверить.
- **Квартира лежит в тех же чеках.** Апрель 2025, ДКП со «Специализированным застройщиком»: 28 086 000
  (безнал) + 6 000 000 (безнал, из них кредит) = 34 086 000, и третьим чеком — «передача объекта» ровно
  на 34 086 000 зачётом аванса. Реально уплачено 34,086 млн, а не 68: без `paid()` покупка удваивается.
  Из 39,2 млн реальных трат за восемь лет это 34,1 млн; ещё ~1,6 млн — мебель и техника того же переезда.
- **Имя продавца — не идентификатор.** Одна компания приходит в трёх-четырёх написаниях («АО "ВКУСВИЛЛ»,
  «АКЦИОНЕРНОЕ ОБЩЕСТВО "ВКУСВИЛЛ»), поэтому группировать надо по `kktOwnerInn`, а не по названию.
- Данные — машинные: `data/lkdr-receipts.json` и ключ `data/lkdr-auth.json` (0600) в `.gitignore` и в
  исключениях `deploy.sh`. Проверено: `git check-ignore` ловит оба, в холостом прогоне rsync их нет.
- **Названия товаров пишет третья сторона.** В HTML их экранировать (`esc()`), в промпт модели отдавать
  только с `--tools ""`, как это уже сделано в `pantry`.

## Траты (`spend.html`, `scripts/spend.py`, `scripts/spend_rules.json`, `/api/spend`)

Поверх чеков из `lkdr.py`. Правила счёта — в `paid()`/`offset()` (см. выше): считается уплаченное, а не
напечатанное, продавец опознаётся по ИНН. Категории лежат в `spend_rules.json` и **правятся руками**:
`byInn` — точное правило, `byName` — запасное по куску названия; всё непонятое видно в `spend.py unknown`
(сейчас там остаток на 5 тысяч, это 0,0% денег). Крупным считается чек от `big` = 100 000 ₽.

**Статистика считается с `since` = 2025-01-01** — и это не «давнее неинтересно», а качество данных: до
2025 года чеки рваные (в 2023-м их пятнадцать штук за весь год), и медиана месяца по ним получалась не
про жизнь, а про то, когда на кассе называли телефон. У инфляции своя, более ранняя отсечка
`sinceInflation` = 2021-01-01: ей нужен длинный ряд, на окне с 2025 года товаров с историей остаётся 26
вместо 48 и «рост цены» начинает зависеть от того, в какой половине окна случилась акция.

```
python3 scripts/spend.py months | merchants | recurring | inflation | unknown | summary
python3 scripts/spend.py leaks             # где течёт: сборы, доставка, такси, мёртвые подписки
python3 scripts/spend.py who "артефакто"   # кто это: имя, ИНН, сайт из чека, категория, что покупалось
```

`who` отвечает на вопрос, который возникает постоянно: в чеке стоит «ЕФИМОВ РИНАТ АЛЕКСЕЕВИЧ», а это
магазин Артефакто (`retailPlace: www.artefacto.ru`). Ищет по имени, ИНН и по словам в названиях товаров.

**«Где течёт» (`spend.py leaks`)** — наблюдения по тратам, но **без модели**: чистая арифметика по своим
же чекам, которую можно перепроверить глазами. Так сделано намеренно: карточка про деньги — единственная
на сайте, где уверенная выдумка модели дорого стоит, «ты стал больше тратить на X» на глаз не проверишь,
а поверишь сразу. Правило отбора: сюда идёт только то, на что можно повлиять, не меняя жизнь. Поэтому
**нет правила «категория выросла в N раз»** — начавшаяся терапия давала «Здоровье ×17,8», формально правда,
а по смыслу не утечка, а решение человека. Что есть: сборы и платная доставка (строки, за которыми нет
товара, — 22,7 тыс. за 2026, больше всего Авито: 47 раз по 333 ₽), доля доставки в еде и куда она движется
(42% против 32% год назад), такси против метро (40 458 против 7 000), стоимость живых подписок и подписки,
которые перестали списываться, и товары, подорожавшие заметно сильнее корзины. Инвестиционных советов
здесь нет и не будет — это лицензируемая область.

Три вещи, которых нет больше нигде:

- **Персональная инфляция** — медианная цена первой и второй половины покупок по товарам, которые берутся
  регулярно: +4% по 48 продуктам. Считается **только по магазинной еде** (`INFL_CATS`): у поездки на такси
  «цена» зависит от длины, а не от года, и первая версия честно показывала «такси подорожало на 103%».
  Услуги и ЖКУ выброшены и по категории, и по словам в названии (`INFL_SKIP`).
- **Регулярные списания** — не меньше четырёх штук, растянутых минимум на `RECUR_SPAN` = 100 дней, и с
  промежутками в месячной полосе `RECUR_GAP` = 20–45 дней, причём таких промежутков должно быть
  большинство (`RECUR_SHARE` = 0,6, но не меньше трёх). Обе оговорки выстраданы: без `RECUR_SPAN` три
  похода за одеждой подряд читались как подписка, а одной медианы мало — у Mothercare промежутки
  24, 139 и 2 дня дают медиану 24, и детские комбинезоны попадали в «регулярное». Полоса именно
  20–45, а не 24–38: счёт за ЖКУ платят не по будильнику (реальные промежутки 22…54 дня).
  Делятся на подписки (сумма не меняется, `steady`) и счета (ЖКУ: 23 тыс/мес, каждый раз своя).
- **Крупное отдельно** — иначе квартира в апреле 2025 сплющивает весь год в одну линию.

**Cloudflare кэширует css на четыре часа** и подменяет наш `Cache-Control: no-cache` своим
`max-age=14400` (html при этом проходит как DYNAMIC и не кэшируется). Один раз это стоило страницы
«Траты»: разметка приехала новая, `theme.css` у браузера остался старый, `var(--c1)` не разрешался —
и столбики стали прозрачными, «столбиков нет». Поэтому `deploy.sh` сам проставляет в ссылку версию
`theme.css?v=<md5 файла>` во всех страницах, а сама страница пишет запасной цвет (`var(--c1, var(--accent))`),
чтобы устаревшая палитра давала одноцветный график, а не пустоту. Правишь theme.css — ничего делать не
надо, деплой проштампует; правишь другой общий файл (app.js, rec.js, taste.js) — помни, что он тоже кэшируется.

**`retailPlace` из чека — главный ключ к непонятному продавцу.** В этом поле лежит адрес сайта
продавца, и часто только он и объясняет, что за контора: «ООО Орбита» с позицией «1 месяц» за 795 ₽
оказалась PARTYstation (pstv.ru, квизы на телевизоре), «Зайцев С.С.» — vodovoz.ru. Поэтому
`spend.py unknown` печатает его рядом с именем — триаж новых продавцов сводится к одной команде.

**Подписка может быть мёртвой.** У каждой строки в «Повторяется само» есть `active`: если последнее
списание старше двух интервалов, писать «9 360 ₽ в год» — враньё. PARTYstation ровно такой:
последний раз списали 8 апреля 2026. На странице такие строки приглушены и вместо годовой суммы
показывают дату последнего списания.

**Имена продавцов — в `names` по ИНН.** В чеке стоит юридическое название, и по «КЕХ ЕКОММЕРЦ» не
догадаешься, что это Авито, «Интернет Решения» — это Ozon, «Купишуз» — Lamoda, «МФК Джамилько» —
Mothercare, «Орбита» — PARTYstation, а
«Ефимов Ринат Алексеевич» — магазин Артефакто (стол Chalet House и старинный шкаф). Приставок вроде «Авиабилеты · » в именах нет намеренно: рядом
и так печатается категория, а разнобой («Авиабилеты · Интернет-билет» против просто «Аэрофлот»)
заставлял думать, что это разные вещи. Правится руками там же, в `spend_rules.json`.

**«Куда уходит» смотрит на выбранный месяц**, а не только на год: за год картина усредняется и перестаёт
отвечать на вопрос «на что ушёл август». Переключатель рядом с заголовком возвращает годовой вид;
помесячные списки приезжают в `merchantsByMonth`.

**График — стопка по категориям** на палитре `--c1…--c6` из `theme.css` (см. «Оформление»). Цвет закреплён
за категорией, а не за её местом в месяце: другой месяц не должен перекрашивать то, что осталось. Шесть
самых крупных категорий за весь показанный период получают слоты по порядку, остальное сваливается в
нейтральный `--c0` (около четверти денег: доставка, здоровье, страховки, транспорт, спорт, связь — их
видно по наведению и в разбивке месяца). **Восьми слотов не будет**: набор из восьми проходит проверку
соседних пар, но проваливает «все пары» (ΔE 2,0), а в стопке соседи меняются, когда какой-то категории
в месяце нет. Порядок сегментов поэтому зафиксирован по слотам, серое всегда снизу — так место сегмента
не прыгает вслед за суммой, а серая часть читается одной полосой. Между сегментами 2 px фона,
иначе соседние заливки читаются как одна. **«Крупное» в стопку не входит**: у него свой масштаб (покупка
квартиры в 34 млн сплющила бы год в линию), поэтому оно штриховкой, с прямой подписью и точной суммой в
таблице ниже. На графике видно всё с отсечки — сейчас 21 месяц.

## Еда (`pantry.html`, `pantry.json`, `scripts/pantry.py`, `scripts/pantrylib/`)

Инвентаризация продуктов из фискальных чеков в почте. Источник — электронные чеки ОФД,
практически целиком ВкусВилл (~500 чеков с 2021 года, 4 в неделю). Читаются по IMAP, доступ только на чтение.
Пароль приложения Gmail: на ноутбуке в связке ключей (`security add-generic-password -a <адрес>
-s gmail-imap-mcp -w`), на mini — в `.env` как `GMAIL_APP_PASSWORD` (связка ключей на headless-машине
запирается после перезагрузки без входа в систему). Google показывает пароль **с пробелами** —
в `.env` их надо убрать, иначе `set -a; . ./.env` возьмёт только первое слово и переменная
не экспортируется, молча.

Конвейер в `scripts/pantrylib/` разделён по потоку данных: `gmail_tool` (почта) → `receipts`
(два разных шаблона писем ОФД → позиции) → **`sources`** (склейка с чеками ФНС) → `analyze` (частота
перекупок → расход в день) → `shelf_life` (ключевое слово → категория) → `inventory` (остатки,
предложение докупить) и `photos` (фото блюда). Всё детерминированное; модель пишет только идеи блюд.

**Источников чеков два** (18.09.2026). Ни один не покрывает другой: по ВкусВиллу 386 чеков общих,
84 есть только в письмах (2022 и 2024 годы), 29 — только в кабинете ФНС. `sources.receipts()` читает оба
и схлопывает совпадения по паре «дата + сумма»; почта считается основной, кабинет добавляет недостающее
(525 уникальных чеков против 508 и 424 по отдельности). **Схлопывание идёт один к одному, а не по
множеству**: внутри одной только почты 46 пар чеков имеют одинаковые дату и сумму (два похода в магазин
за день на одну сумму — обычное дело), и с обычным `set` второй такой чек из кабинета пропадал бы. Время
в ключ не берётся: у 7 из 386 совпавших пар оно расходится на пару минут, а у одной — на два часа. Оттуда же приезжает Сбермаркет, которого в
письмах не было вовсе. И главное — `sources` выбрасывает **зачёты аванса** (`lkdr.offset()`): доставка
ВкусВилла печатает два чека на одну покупку, и без этого товар считался бы съеденным вдвое быстрее.

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

Расписание на mini: `sh deploy/install-pantry.sh` (launchd, ежедневно в 07:05 — сразу после выкачки чеков в 07:00).
Кнопка «Обновить» на странице дёргает `POST /api/pantry/review`.
