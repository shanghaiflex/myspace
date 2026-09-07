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
MODEL="${PANTRY_MODEL:-opus}"
WORK="${PANTRY_WORKDIR:-$HOME/.pantry-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

# Mail first: a note about stale data helps nobody.
if ! python3 scripts/pantry.py sync > "$WORK/sync.txt" 2>&1; then
  echo "$STAMP sync failed:"; cat "$WORK/sync.txt"; exit 1
fi
python3 scripts/pantry.py summary > "$WORK/summary.txt"

if [ "$1" != "--force" ]; then
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
echo "$STAMP $(cat "$WORK/summary.txt")"; cat "$WORK/note.txt"; echo
