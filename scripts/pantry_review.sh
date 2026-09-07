#!/bin/sh
# Daily food note: Gmail receipts → pantry.json → Claude Code (Opus) → short note on the Еда page.
#   scripts/pantry_review.sh [--force]     --force: write a note even if nothing changed
# Runs on the mini from launchd (deploy/install-pantry.sh, daily) and from POST /api/pantry/review.
# Needs `claude` in PATH and CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env, plus the Gmail
# app password in the login keychain: security add-generic-password -a <addr> -s gmail-imap-mcp -w
# PANTRY_MODEL overrides the model (default: opus).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
# Flags in any order: --force writes a note even when nothing changed,
# --force-recipes regenerates the dish ideas before they go stale.
FORCE=0; FORCE_RECIPES=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --force-recipes) FORCE=1; FORCE_RECIPES=1 ;;
  esac
done
MODEL="${PANTRY_MODEL:-opus}"
WORK="${PANTRY_WORKDIR:-$HOME/.pantry-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

# Mail first: a note about stale data helps nobody.
if ! python3 scripts/pantry.py sync > "$WORK/sync.txt" 2>&1; then
  echo "$STAMP sync failed:"; cat "$WORK/sync.txt"; exit 1
fi
python3 scripts/pantry.py summary > "$WORK/summary.txt"

if [ "$FORCE" -eq 0 ]; then
  python3 scripts/pantry.py pending > "$WORK/pending.txt" 2>&1 || {
    echo "$STAMP $(cat "$WORK/pending.txt")"; exit 0; }
fi
command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }

python3 scripts/pantry.py digest > "$WORK/digest.txt"
{ cat pantry/PROMPT.md; printf '\n## Данные\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"

# Run outside the repo: no CLAUDE.md, no project settings, no tools — the digest is the whole context.
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > note.txt 2> claude.err; then
  echo "$STAMP claude failed:"; cat claude.err; exit 1
fi
cd "$ROOT"
python3 scripts/pantry.py review-save --model "$MODEL" --file "$WORK/note.txt"

# Dish ideas change far slower than stock: regenerate only every few days,
# or when asked. Nobody cooks from a list that is different every morning.
if [ "$FORCE_RECIPES" -eq 1 ] || python3 scripts/pantry.py recipes-stale >/dev/null 2>&1; then
  { cat pantry/RECIPES.md; printf '\n## Мой профиль\n'; python3 scripts/pantry.py profile; } > "$WORK/recipes-prompt.txt"
  cd "$WORK"
  if claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < recipes-prompt.txt > recipes.json 2> recipes.err; then
    cd "$ROOT"
    python3 scripts/pantry.py recipes-save --model "$MODEL" --file "$WORK/recipes.json" || echo "$STAMP рецепты не сохранились"
  else
    cd "$ROOT"; echo "$STAMP claude (рецепты) не отработал:"; cat "$WORK/recipes.err"
  fi
fi

# Recount at the end: the earlier summary ran before the recipes were saved,
# so reusing it logs "0 идей блюд" right after saving four of them.
echo "$STAMP $(python3 scripts/pantry.py summary)"; cat "$WORK/note.txt"; echo
