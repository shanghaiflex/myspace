# Миксы и советы по музыке

_Вынесено из `CLAUDE.md` 18.10.2026 слово в слово; индекс модулей и общие правила — в `CLAUDE.md`._

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
- **Не всякий трек SoundCloud играет в виджете** (20.09.2026). Плеер на странице — официальный iframe, и загрузчик
  вправе его запретить: страница трека открывается, а виджет пишет «This track can't be played outside of
  SoundCloud» и стреляет `ERROR`, из-за чего на сайте появляется «SoundCloud не ответил». Совет «Silent Hill 3
  (OST)» (`sc:146074937`) был ровно такой. В API это видно заранее — `streamable: false`, `embeddable_by: "none"`,
  `policy: "BLOCK"`, — и теперь спрашивается: `X.sc_playable(t)` в `mixes.py` зовут поиск советов
  (`mix_recs.search_soundcloud`), `resolve_soundcloud` (вставка ссылки на сайте даёт 400 с внятной причиной)
  и импорт лайков. Проверка дешёвая: поля уже приходят в том же ответе. Ручная проверка одного трека —
  `curl -o /dev/null -w '%{http_code}' 'https://soundcloud.com/oembed?format=json&url=<url>'`: 403 значит, что
  не сыграет. Коллекция (20 треков SoundCloud) проверена целиком — там таких нет.
- `import-youtube` reads the private «Liked videos» playlist (`LL`) through the cookies of a logged-in
  browser (`--browser firefox`, works on the laptop; the mini has no browser and no YouTube without VPN).
  It never adds in bulk on purpose: the same likes hold lectures, let's plays and Мэддисон, so it prints
  the long unknown videos numbered and adds only what `--pick` names (numbers or video ids). Tag the
  ambient ones `--tag calm` — see below.
- The SoundCloud username for imports is not stored anywhere yet; ask the user if unknown.
- **Kidmancast** (Коля Kidman, future garage / bass, 2014–2022) лежит в коллекции целиком, тег `kidmancast`,
  артист везде «Kolya Kidman» (23.09.2026): основная копия — Mixcloud `kidmancast`, №5 там закрыт для страны
  и взят с SoundCloud (`kolyakidman`), там же №6, а pt.4/30/31 есть только на YouTube (`@kidmancastmusic`).
  YouTube с ноутбука теперь требует «я не робот» — метаданные ролика берутся yt-dlp'ом на mini:
  `YTDLP=<обёртка: exec ssh mini bin/yt-dlp "$@"> mixes.py add https://youtu.be/<id>` (ссылку без `?`, иначе
  её глобом съест zsh на mini).
- Playback position is remembered per mix on the server (`PATCH /api/mix/<id>` `{position}` → `position`,
  `playedAt` in `mixes.json`), so a closed tab reopens on the mix that was playing and continues from there
  («Сначала» in the hero starts over; the position is cleared when a mix plays to the end).
- «Фокус» (or <kbd>F</kbd>) puts the player into real fullscreen with the nav and the collection hidden;
  the hero carries a 25/5 pomodoro (<kbd>P</kbd>), which auto-switches phases with a beep and survives a reload
  (localStorage, resets daily).
- The player hides the SoundCloud/YouTube/Mixcloud widgets off-screen and draws its own progress bar
  (seek via widget APIs). Page background: random photo from `backgrounds/` (listed by `/api/backgrounds`),
  crossfades on every new mix. The user can drop their own JPG/PNG there. Only landscapes and «Твин Пикс»
  (туман, хвойный лес, PNW; тема `fog` в `scripts/backgrounds.py`) — архитектура, интерьеры и живопись убраны
  23.09.2026 по просьбе пользователя, в CREDITS.json они помечены `kept: false`, чтобы не вернулись.
- Mix ids: `sc:<n>`, `yt:<videoId>`, `mc:<uploader_slug>`. Fields: source, url, title, artist, duration (s),
  artwork, genre, published, tags[], addedAt, optional `nts` (episode URL), `position`/`playedAt` (resume).
- **До 9 утра (`MORNING_UNTIL` в `index.html` и `mixes.html`) сайт показывает только спокойное**: в это
  время человек читает, а не танцует. Спокойный микс в коллекции помечен тегом `calm`, спокойный совет —
  полем `mood: "calm"`. До девяти «Микс на сегодня» на главной превращается в «Под утреннее чтение» и
  берётся из `calm`-части коллекции, случайный микс на странице миксов (и кнопка «дальше») тоже, а советы
  Claude сортируются спокойными вперёд. Если спокойного нет вовсе — всё как обычно, без пустых блоков.

### Daily advice from Codex (`mix_recs.json`, `mixes/PROMPT.md`, `scripts/mix_recs.py`, `scripts/mix_recs.sh`)

Once a day the mini asks Codex for new mixes. `mix_recs.py digest` = collection + what was actually
played (position/playedAt) + verdicts on earlier advice; `mixes/PROMPT.md` asks for 9 candidates as JSON
(`search`, `artist`, `title`, `mood`, `why`); `mix_recs.py apply` resolves them through SoundCloud search
(`/search/tracks`, ≥20 min, the asked-for artist must really be in the track, nothing already known) and
keeps the first 3 rhythmic + 2 calm in `mix_recs.json` (`items` + `history`) — two quotas (`--keep`,
`--keep-calm`), because with one list the rhythmic ones eat the whole batch and mornings stay empty.
**Любимые резидентуры NTS** (`SHOWS` в `mix_recs.py`, 04.10.2026): сейчас одна — Coucou Chloé (36 выпусков
2016–2020, почти все на Mixcloud). От неё всегда висит один случайный выпуск сверх квот модели (`show` в
записи, квоты его не считают); ответил или прошла неделя — `mix_recs.py shows` (первой строкой `mix_recs.sh`,
без модели) кладёт следующий. Выпуск, уже лежащий в коллекции перезаливом с чужого аккаунта, узнаётся по
гостю в названии («invites: Brat Star»). Новую резидентуру — строкой в `SHOWS` (слаг из nts.live/shows/<слаг>).
One of the 5 rhythmic slots is UK grime / early-slowthai territory (Rinse/NTS grime shows, Logan Sama, Sir
Spyro, Slackk, MC sets; user request 04.10.2026) — a rule in `mixes/PROMPT.md`, no code. The calm half is a separate brief in the prompt (ambient, drone, modern classical, quiet jazz, game
soundtracks — no dance pulse), and the digest shows the model what `calm` mixes already are in the
collection. «Нравится» on a calm suggestion adds it with tags `claude-rec` + `calm`. The mixes page shows them as «Советует Codex»
cards (Послушать / Нравится / Не то) and the home page uses the top one as «Микс на сегодня».
**«Советует одно и то же» (09.10.2026).** Две причины, обе закрыты. (1) Включённый, но не отвеченный совет
жил всю неделю `ITEM_DAYS`: три из пяти мест держали включённые, и подборка не менялась по пять дней. Теперь
он уходит в историю как `played` через `PLAYED_DAYS` = 2 дня с первого включения (`playedAt`). (2) Модель
клонировала последний лайк: после Grime Show с Sir Spyro — ещё одна Grime Show с Sir Spyro и Logan Sama
рядом, после альбома Yoshimura — другой альбом Yoshimura. В `apply` теперь `repeats()`: если артист из
поля `artist` уже висит на странице или был в ответах за `REPEAT_DAYS` = 30 дней, кандидат отсеивается
(«repeat of …» в логе). Названия площадок (`PLATFORMS`: NTS, Rinse FM, Boiler Room…) в сравнении не
участвуют, иначе одно слово «NTS» закрыло бы всё радио. В промпте: лайк задаёт направление, а не артиста;
девять разных артистов, минимум четыре сцены; грайм — ровно один, и ни одного, если грайм уже висит на странице.

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
