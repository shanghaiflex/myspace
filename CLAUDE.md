# Movies — personal film catalog

Pages: `index.html` (home / morning dashboard), `films.html`, `books.html`, `mixes.html`, `lectures.html`, `health.html`, `reads.html`, `french.html`, `pantry.html`, `home.html` (умный дом). Films page over `movies.json` with posters in `posters/`, served by `serve.py`
(stdlib only) which also exposes a tiny edit API (PATCH/DELETE `/api/movie/<id>`) used by the page for
rating / status / note / delete. Run `./serve.sh` and open http://localhost:8787.
If the page is served by a plain static server the API is absent and the page becomes read-only.
Ещё страницы: `spend.html` (траты), `invest.html` (инвест). Сайт и приложение вместе зовутся BoW
(https://bodywithoutorgans.cc).

Этот файл — индекс (с 18.10.2026). Подробности, история починок и «почему» по каждому модулю лежат в
`docs/<модуль>.md`. Раньше всё было здесь, и файл разросся до 184 КБ / 1540 строк, которые грузились в каждую
сессию. **Прежде чем трогать модуль, прочитай его `docs/<модуль>.md`**: там пороги, ловушки и причины, почему
сделано именно так. Новую запись о модуле пиши в его doc, сюда — только то, что нужно в любой сессии.

## Editing workflow (IMPORTANT — how to ship changes)

When the user asks to change anything (add/remove/rate a film, book, mix, lecture; edit a page; etc.):
1. Make the change on the laptop (the CLIs: `scripts/{movies,books,mixes,lectures}.py`, or edit the HTML/py).
2. Run `scripts/deploy.sh "short message"`. This commits, pushes to GitHub, and updates the live mini in one step.
   Do this automatically after the edit — the user expects the change to be live at https://bodywithoutorgans.cc.
`deploy.sh` pulls the mini's live JSON for any catalog file you did NOT edit this session first, so in-site
edits (ratings/status changed through the website) are never clobbered. If the mini is unreachable it still
commits+pushes and tells you it skipped the deploy. Data files (`*.json`) need no restart; code changes get one
automatically. Audio lives only on machines (gitignored) — download lecture audio on the laptop; deploy pushes it to the mini.

- **Всегда начинай с `git status`.** Сайт правит JSON на mini сам, а в этой папке параллельно работают другие
  сессии. `deploy.sh` делает `git add -A`, то есть закоммитит и чужие незаконченные правки. Если в дереве есть
  чужое, коммить свои файлы поимённо.
- **Репозиторий публичный** (github `shanghaiflex/myspace`). Пароли, токены и личные данные не коммить:
  секреты лежат в `.env`, данные машины — в `.gitignore` и в исключениях `deploy.sh`.
- Каталоги правь только через CLI, `movies.json` и прочие `*.json` руками не трогай (см. `docs/catalog.md`).

## Модули

| Модуль | Файлы | Документ |
|---|---|---|
| Фильмы, книги | `films.html`, `books.html`, `movies.json`, `books.json`, `scripts/{movies,books}.py` | `docs/catalog.md` |
| Оформление | `theme.css`, `scripts/palette_check.py` | `docs/design.md` |
| Главная | `index.html`, `scripts/weather.py`, «Чем занять окно» | `docs/front.md` |
| Здоровье | `health.html`, `health.db`, `scripts/{health,vitals,load,week,nights}.py`, `health/` | `docs/health.md` |
| Приложение BoW (iOS) | `~/workspace/bow`, `app.js`, `scripts/notify.py` | `docs/app.md` |
| Контекст обо мне | `scripts/context.py` | `docs/context.md` |
| Расписание | `scripts/schedule.py`, Яндекс.Календарь | `docs/schedule.md` |
| Дом | `home.html`, `scripts/{home,lamp_schedule,sun,vacuum,vacuum_schedule}.py` | `docs/home.md` |
| Миксы | `mixes.html`, `mixes.json`, `mix_recs.json`, `mixes/PROMPT.md` | `docs/mixes.md` |
| Советы (фильмы, книги, лекции) | `taste_recs.json`, `taste.js`, `scripts/taste_recs.py` | `docs/recs.md` |
| Старты | `races.json`, `races/`, `scripts/races.py` | `docs/races.md` |
| Статьи | `reads.html`, `reads.json`, `reads/PROMPT.md` | `docs/reads.md` |
| Французский | `french.html`, `french.json`, `french/` | `docs/french.md` |
| Лекции | `lectures.html`, `lectures.json`, `scripts/lectures.py` | `docs/lectures.md` |
| Чеки ФНС, траты | `spend.html`, `scripts/{lkdr,spend}.py`, `scripts/spend_rules.json` | `docs/spend.md` |
| Инвест | `invest.html`, `scripts/invest.py`, `~/workspace/invest` | `docs/invest.md` |
| Еда | `pantry.html`, `pantry.json`, `scripts/pantry.py`, `scripts/pantrylib/` | `docs/pantry.md` |
| Выкладка, mini, VPN | `scripts/deploy.sh`, `deploy/`, `scripts/vpn_watchdog.sh` | `docs/deploy.md` |

Советы и заметки пишет `claude -p` на mini по launchd; промпты лежат в `<модуль>/PROMPT.md`. Что модель знает
обо мне, собирает `scripts/context.py`: `python3 scripts/context.py show`.

## Ловушки, которые бьют в любой сессии

**Прод и mini**
- Прод — домашний Mac mini (`ssh mini`, сайт в `~/movies`). На нём нет git, brew и CLT. `python3` нет в PATH
  по ssh, вызывай `~/.local/python312/bin/python3`. Claude Code там лежит в `~/.local/bin/claude`.
- Сайт ходит в мир через VPN-тоннель mini. Если сайт лежит, смотри сначала лог сторожа:
  `ssh mini tail -20 movies/logs/vpn-watchdog.log`. Подробности — `docs/deploy.md`.
- `health.db` — единственная копия данных здоровья: она есть только на mini, а телефон уже отданное повторно не
  шлёт. Не удаляй и не перезаписывай её. Ночной бэкап лежит в `backups/`.
- Данные машины (`health.db`, `audio/`, `pantry.json`, `data/calendar.ics`, `data/invest.json`,
  `data/roborock-*.json`, …) — в `.gitignore` и в `--exclude` у `deploy.sh`. Новый такой файл добавляй в оба
  места, иначе `rsync --delete` сотрёт его на mini.

**Код и конфиг**
- Значение в `.env` с пробелом бери в кавычки. Скрипты читают его через `set -a; . ./.env`, и без кавычек
  переменная молча не экспортируется. Так ломались `SCHEDULE_CALENDARS="Мои события"` и пароль Gmail, который
  Google показывает с пробелами.
- Модуль календаря называется `schedule.py`, а не `calendar.py`: второе имя затеняет стандартный модуль и
  ломает `strptime`.
- Цвета и гарнитуры меняй только в `theme.css`, новые цвета для данных — только после
  `python3 scripts/palette_check.py`. Новая страница подключает `theme.css` последним и `app.js` после него.
  Палитру повторяет `Theme.swift` в приложении: правишь одно — правь и другое.
- Cloudflare держит `.css` четыре часа. `deploy.sh` сам ставит в ссылки `theme.css?v=<md5>`, руками не правь.

**Люди и аккаунты**
- Капчи, двухфакторку, пароли и sudo на mini делает пользователь. Это касается входа в ЛК ФНС (`lkdr.py login`),
  Apple ID в Xcode и `deploy/install-vpn.sh`.
- Приложение подписано бесплатным Personal Team, профиль живёт 7 дней. «Посинкай приложение» значит
  `sh ~/workspace/bow/phone.sh`. Телефон должен быть разблокирован (`docs/app.md`).
- «Еда» в приложении не показывается — ни табом, ни строкой в «Ещё». Это просьба пользователя.
- Чужие события в общем календаре «Семья» (Полины) показывай с её именем и не считай моим занятым временем
  (`docs/schedule.md`). Правило календаря не удаляй, а закрывай датой `UNTIL`.
- Часы пользователь носит не каждый день. День без часов — это пропуск в данных, а не аномалия (`docs/health.md`).

## Частые команды

```
scripts/deploy.sh "message"                       # выкатить всё
python3 scripts/movies.py add "Title" --rating 8  # фильм (подробно — docs/catalog.md)
python3 scripts/schedule.py list [--days 7]       # календарь; add/end/skip/rename — docs/schedule.md
python3 scripts/context.py show                   # что промпты знают обо мне
python3 scripts/notify.py list                    # что увидит телефон
ssh mini 'cd movies && sh scripts/health_review.sh --force'   # заметка о здоровье сейчас
```
