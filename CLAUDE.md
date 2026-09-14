# Movies — personal film catalog

Pages: `index.html` (home / morning dashboard), `films.html`, `books.html`, `mixes.html`, `lectures.html`, `health.html`, `reads.html`, `pantry.html`, `home.html` (умный дом). Films page over `movies.json` with posters in `posters/`, served by `serve.py`
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

**The site inside the app.** The first tab «Сайт» is a WKWebView on https://bodywithoutorgans.cc, logged in via
`GET /app/login?t=<bearer>&next=/` (token → the usual session cookie). Pages know they run in the app
(`window.BoW`, user agent contains `BoW/2`); `lectures.html` hands lectures that are downloaded on the phone
(`window.BoW.downloaded`) to the native player through `webkit.messageHandlers.bow` (`{type:'play', id, position}`).
Everything else is the plain site. New phone-specific behaviour goes the same way: a small hook in the page, the
heavy part native.

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
`health_review.sh` спотыкается на втором слове: `события: command not found`; пусто = все календари); `SCHEDULE_FREE_CALENDARS` (по умолчанию «Тренировки») — показывать, но
временем не занимать: четыре часа чужого плавания каждый понедельник и пятницу не оставляли в утре места.
Так же ведут себя события, помеченные в Яндексе «свободен» (`TRANSP:TRANSPARENT`) — например «Французский
Сережи». События помечаются источником (`X-BOW-CALENDAR`) при сборке: `CATEGORIES` Яндекс заполняет только в
одном календаре. Свободное окно — дыра не меньше `FREE_MIN` (45 мин) между `DAY_START` и `DAY_END`.

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
python3 scripts/schedule.py free [--date ...] # свободные окна дня
python3 scripts/schedule.py calendars | rules  # коллекции / повторяющиеся правила
python3 scripts/schedule.py end "Психолог" [--at 09:00] [--date YYYY-MM-DD] [--dry-run]
python3 scripts/schedule.py add "ЛФК" --day WE --time 18:00 --minutes 60 [--once] [--dry-run]
```

Состояние на 14.09.2026: закрыты датой «Психолог чт 09:00» и «ЛФК ср 18:30» (вместо неё заведена ср 18:00),
удалены правила «Теннис сб 16:00» и «Тренировка в зале» ср 09:00 / вс 10:00 — по просьбе пользователя, копии
удалённого лежат в `data/calendar-deleted/` (данные машины, в `.gitignore`). Действуют: «Работа пн–пт 10:00–18:00»,
«Французский Сережи вт/чт 19:00» (не занимает), «ЛФК ср 18:00».

## Дом (`home.html`, `scripts/home.py`, `/api/home`)

Умный дом на сайте: скетч кухни (SVG, окно, полка, диван, картина с двумя бра), свечение ламп рисуется
по реальному состоянию — цвет из `color_temp`/`xy`, яркость в прозрачность.
Клик по бра переключает группу `lamps`, клик по оранжевому диску Varmblixt — розетку `plug`; рядом
панель: тумблеры, яркость, тепло (кельвины → mired), сцены — через `serve.py`, а не через CLI `lamp` из скилла.
Живой огонь (камин/свеча/факел) со страницы убран 14.09.2026 по просьбе пользователя; `effect.js` остался только для CLI. Сцены только белые, четыре: «Уютно» пользователя (43 %, 2300 K), «Чтение» и «Ночник» по рецептам Philips Hue
(температуры Hue, яркость × 0,76 — цифры Hue один к одному на Tuya светят ярче, чем задумано), «Кино» — 12 %,
2700 K, тусклый тёплый свет за спиной зрителя. Приглушённо/Отдых/Ярко/Собраться/Ко сну удалены 14.09.2026 по просьбе; цветные пресеты в духе WiZ (янтарь,
закат, кино, зелёный) убраны 14.09.2026 как некрасивые — не возвращать без просьбы. Колонка управления справа
задаёт высоту, а скетч дорисовывает стену вверх (`viewBox` пересчитывается по размеру карточки), чтобы ничего не резать.

`scripts/home.py` — MQTT 3.1.1 клиент на голых сокетах (paho на mini нет): `state()` подписывается на
`zigbee2mqtt/{lamp,lamp2,plug}`, шлёт `/get` и ждёт до 2 с; `set_device()` пропускает только
`state/brightness/color_temp/color/transition`. **Каждая команда проверяется**: группа `lamps` — это Zigbee-broadcast
без подтверждения, лампа, пропустившая его, остаётся на старом свете (так «Приглушённо» однажды светило
как «Чтение»). Через `transition` + 1,5 с `_verify` спрашивает лампы и той, что показывает не то, шлёт тот же
payload адресно (unicast подтверждается); проверяется только последняя команда по цели (`_seq`), иначе быстрые
клики по сценам дрались бы друг с другом. В логе serve: `home verify lamp2: shows … — resending …`. Брокер: `MQTT_HOST`, иначе localhost
там, где есть `~/mqtt/effect.js` (mini), иначе LAN-имя mini → VPN 10.8.1.5. **На ноутбуке стоит свой
пустой mosquitto на localhost** — он отвечает, но устройств не знает, поэтому localhost на ноутбуке не пробуется.
Расписание (19:00/22:00/23:00) живёт в launchd на mini и страницей не правится, только показывается.

```
python3 scripts/home.py state                       # что видит страница
python3 scripts/home.py set lamps '{"state":"ON","brightness":120}'
python3 scripts/home.py scene cozy | effect fire | effect off
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

## Советы по фильмам и книгам (`taste_recs.json`, `taste.js`, `scripts/taste_recs.py`, `scripts/taste_recs.sh`)

Тот же механизм, что у миксов, но для двух каталогов сразу. `taste_recs.py digest film|book` собирает
контекст вкуса: фильмы — все оценённые с режиссёром, жанрами и моими заметками плюс список «посмотреть»;
книги — что прочитано, что **брошено** (главный отрицательный сигнал) и что в планах. Оценок у книг нет
(`rating` пустой у всех, теги — цветные квадраты из Obsidian), поэтому там вкус читается по составу;
если оценки появятся через страницу, дайджест подхватит их сам.

`films/PROMPT.md` и `books/PROMPT.md` просят 6 кандидатов в виде JSON (`title`, `year`/`author`, `why`),
`taste_recs.py apply` проверяет каждого: фильм — через OMDb (`movies.py resolve` + `metadata`), книгу —
через Google Books / Open Library (`books.py lookup`). Выдуманное название и то, что уже в каталоге,
в истории или в советах, отсеивается; первые 3 подтверждённых ложатся в `taste_recs.json`.

Картинки советов, скачанные на mini, раньше стирал каждый деплой (`rsync --delete` с ноутбука, где их не было);
с 2026-09-13 `deploy.sh` сначала забирает `posters/` и `covers/` с mini. Совет без картинки или непроверенный
(каталоги молчали) чинится на месте: `python3 scripts/taste_recs.py covers [film|book]` — на mini, это production.

Обратная связь — весь смысл: «Хочу» переносит фильм в `movies.json` со статусом `to-watch` (постер
скачивается локально, причина совета уезжает в `note`), книгу — в `books.json` со статусом `to-read`
(тег `claude-rec`); «Не то» — отказ. И то, и другое ложится в `history` и попадает в следующий промпт.

```
python3 scripts/taste_recs.py digest film|book        # что видит модель
python3 scripts/taste_recs.py list [film|book]        # текущие советы
sh scripts/taste_recs.sh [film|book|both] [--force]   # спросить сейчас (на mini: ssh mini 'cd movies && sh scripts/taste_recs.sh both --force')
python3 scripts/taste_recs.py verdict film <tt…> liked|dismissed
ssh mini tail -20 movies/logs/taste-recs.log
```

launchd на mini: `cc.bodywithoutorgans.tasterecs`, ежедневно в 07:40 (`deploy/install-taste-recs.sh`).
API: `GET /api/recs/<film|book>`, `PATCH /api/rec/<kind>/<id>` `{verdict}`, `POST /api/recs/<kind>/refresh`
(кнопка «Обновить» в блоке «Советует Claude» на обеих страницах, общий код — `taste.js`).

### Порядок карточек на главной

Задан пользователем 14.09.2026 и держится в одном месте — в конце обработчика `Promise.all` в `index.html`:
погода, «Well-being», лекция, «Почитать», два микса (случайный + совет Claude), «Читаю». На телефоне сетка
складывается в одну колонку (`@media (max-width: 900px)`), то есть порядок в приложении BoW — тот же самый.

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
- **Телефон держит две лекции офлайн** (BoW, `~/workspace/bow`): `GET /api/lectures/preload` отдаёт
  `lectures.py preload` — сначала `listening` (последняя тронутая первой, `touchedAt` ставится при каждом
  PATCH позиции), потом `queued` по времени постановки, всего `PRELOAD_COUNT` = 2; сам запрос запускает
  `start_audio_job` для тех, у кого нет звука, так что mini качает следующую лекцию заранее (yt-dlp с mini
  до YouTube дотягивается). Приложение скачивает файлы фоновой URLSession, шлёт позицию каждые 15 с и на паузе,
  по концу ставит `listened`, удаляет файл и берёт следующую. Список обновляется при открытии, по BGTask и после
  каждой прослушанной. Позиция на сервере обрезается по длительности лекции (была 4:08 у лекции на 2:51).
  Запросы к `/audio/` mini логирует строкой `audio <file> → iphone [Range]` (Range = докачка после обрыва).
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
