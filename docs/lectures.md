# Лекции

_Вынесено из `CLAUDE.md` 18.10.2026 слово в слово; индекс модулей и общие правила — в `CLAUDE.md`._

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
- **Серия «под книгу» — всегда одна про запас** (23.09.2026): серии из `db["reserve"]` дают по одной лекции
  **сверх** `PRELOAD_COUNT` (`reserve_next`: начатая, иначе первая непрослушанная по `queuedAt`), каналы она
  не вытесняет. Сейчас это «Под книгу: Ближний Восток и Эгейя, 1800–1380 до н. э.» — к Кембриджской истории
  древнего мира, т. II ч. 1, которую я читаю: Bushwacker «Месопотамия Бронзового Века» → «Хетты» → «С чего
  начинается Греция» → «Любовь, смерть и клинопись в Месопотамии» → «Финикия» (египетская серия исключена
  нарочно, у Макарова по бронзовому веку ничего нет). Другая книга — новая серия и
  `lectures.py reserve "<серия>"`, старую снять `--off`.
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
