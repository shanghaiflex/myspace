#!/bin/sh
# Daily mix advice: taste digest → Codex → SoundCloud search → mix_recs.json → the site.
#   scripts/mix_recs.sh [--force]      --force: run even if the last batch is younger than a day
# Runs on the mini from launchd (deploy/install-mix-recs.sh, once a day) and from POST /api/mix-recs/refresh.
# Needs authenticated `codex` and yt-dlp for SoundCloud (see docs/ai.md). MIX_RECS_MODEL overrides the model.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL=$(python3 "$ROOT/scripts/codex_ask.py" --resolve-model "${MIX_RECS_MODEL:-}")
WORK="${MIX_RECS_WORKDIR:-$HOME/.mix-recs}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

# Любимые резидентуры NTS — без модели и без ожидания суток: место освободилось — приехал следующий выпуск.
python3 scripts/mix_recs.py shows || true

if [ "$1" != "--force" ]; then
  python3 scripts/mix_recs.py due > "$WORK/due.txt" || { echo "$STAMP skip: $(cat "$WORK/due.txt")"; exit 0; }
fi
command -v codex >/dev/null || { echo "codex not installed; see docs/ai.md"; exit 1; }

python3 scripts/mix_recs.py digest > "$WORK/digest.txt"
{ cat mixes/PROMPT.md; printf '\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"

# Run from the work dir, outside the repo: no AGENTS.md, no project settings, no tools — the digest is the whole context.
cd "$WORK"
if ! python3 "$ROOT/scripts/codex_ask.py" --model "$MODEL" --json < prompt.txt > answer.json 2> codex.err; then
  echo "$STAMP codex failed:"; cat codex.err; exit 1
fi
cd "$ROOT"
echo "$STAMP suggestions ($MODEL):"
python3 scripts/mix_recs.py apply --file "$WORK/answer.json" --model "$MODEL"
