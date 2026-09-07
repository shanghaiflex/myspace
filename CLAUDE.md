# Movies — personal film catalog

Pages: `index.html` (home / morning dashboard), `films.html`, `books.html`, `mixes.html`, `lectures.html`. Films page over `movies.json` with posters in `posters/`, served by `serve.py`
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
- The player hides the SoundCloud/YouTube/Mixcloud widgets off-screen and draws its own progress bar
  (seek via widget APIs). Page background: random photo from `backgrounds/` (listed by `/api/backgrounds`),
  crossfades on every new mix. The user can drop their own JPG/PNG there.
- Mix ids: `sc:<n>`, `yt:<videoId>`, `mc:<uploader_slug>`. Fields: source, url, title, artist, duration (s),
  artwork, genre, published, tags[], addedAt, optional `nts` (episode URL).

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
The mini has no git/brew/CLT; Python lives in `~/.local/python312`, tools in `~/bin`. Data sync is rsync:
after ANY change run `scripts/deploy.sh "message"` — it folds in live in-site edits for JSON you didn't touch,
commits, pushes to GitHub, rsyncs to the mini, and restarts serve. That is the one command to ship to production.
The mini reaches SoundCloud/Cloudflare/Open-Meteo directly but YouTube is blocked there without a VPN (its AmneziaVPN
is currently not configured), so download lecture audio on the laptop (`scripts/lectures.py audio <id>`) and let
`scripts/sync.sh` push `audio/` to the mini.

## Laptop
`docker compose up -d` (colima) keeps an always-on local instance on http://localhost:8787 with the repo
bind-mounted, so edits from Claude Code and from the site land in the same files. The Firefox homepage is
production, https://bodywithoutorgans.cc (it was localhost until the tunnel went live), set in the profile's
`user.js` (`~/Library/Application Support/Firefox/Profiles/oswx4c6l.default-release/user.js`).
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
