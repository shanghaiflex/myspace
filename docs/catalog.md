# Фильмы и книги: каталоги, CLI, модель данных

_Вынесено из `CLAUDE.md` 18.10.2026 слово в слово; индекс модулей и общие правила — в `CLAUDE.md`._

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

## Data model (`movies.json`, array sorted by title)
`id` (imdb), `title`, `year`, `director`, `genre[]`, `runtime` (min), `plot`, `imdbRating`,
`poster` (local path), `posterUrl`, `type` (movie|series), `status` (watched|to-watch|watching),
`rating` (my 1–10 or null), `addedAt`, `watchedAt`, `note`, optional `link`.

## Origin
Imported once from the Obsidian vault (`~/Documents/Obsidian Vault/Movies/data`) via
`scripts/import_obsidian.py`. This repo is now the source of truth; Obsidian is not synced.
