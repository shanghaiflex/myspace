#!/bin/sh
# Daily mix advice: taste digest → Claude Code (Opus) → SoundCloud search → mix_recs.json → the site.
#   scripts/mix_recs.sh [--force]      --force: run even if the last batch is younger than a day
# Runs on the mini from launchd (deploy/install-mix-recs.sh, once a day) and from POST /api/mix-recs/refresh.
# Needs `claude` (Claude Code CLI) and either CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) or
# ANTHROPIC_API_KEY in .env, plus yt-dlp for the SoundCloud client_id. MIX_RECS_MODEL overrides the model.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${MIX_RECS_MODEL:-opus}"
WORK="${MIX_RECS_WORKDIR:-$HOME/.mix-recs}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

if [ "$1" != "--force" ]; then
  python3 scripts/mix_recs.py due > "$WORK/due.txt" || { echo "$STAMP skip: $(cat "$WORK/due.txt")"; exit 0; }
fi
command -v claude >/dev/null || { echo "$STAMP claude not installed (curl -fsSL https://claude.ai/install.sh | bash)"; exit 1; }

python3 scripts/mix_recs.py digest > "$WORK/digest.txt"
{ cat mixes/PROMPT.md; printf '\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"

# Run from the work dir, outside the repo: no CLAUDE.md, no project settings, no tools — the digest is the whole context.
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > answer.json 2> claude.err; then
  echo "$STAMP claude failed:"; cat claude.err; exit 1
fi
cd "$ROOT"
echo "$STAMP suggestions ($MODEL):"
python3 scripts/mix_recs.py apply --file "$WORK/answer.json" --model "$MODEL"
