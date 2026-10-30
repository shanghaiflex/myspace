#!/bin/sh
# Hourly health note: digest of health.db → Codex → short note saved back to health.db and shown on the home page.
#   scripts/health_review.sh [--force]      --force: run even if no new samples arrived since the last note
# Runs on the mini from launchd (deploy/install-health-review.sh, every hour) and from POST /api/health/review.
# Needs authenticated `codex` in PATH (see docs/ai.md). HEALTH_MODEL overrides BOW_CODEX_MODEL.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL=$(python3 "$ROOT/scripts/codex_ask.py" --resolve-model "${HEALTH_MODEL:-}")
WORK="${HEALTH_WORKDIR:-$HOME/.health-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

if [ "$1" != "--force" ]; then
  # Не «есть ли новые образцы», а «изменилось ли что-то существенное» — см. health.changed().
  python3 scripts/health.py pending > "$WORK/pending.json" || { echo "$STAMP skip: $(cat "$WORK/pending.json")"; exit 0; }
  echo "$STAMP why: $(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['why'])" "$WORK/pending.json")"
fi
command -v codex >/dev/null || { echo "codex not installed; see docs/ai.md"; exit 1; }

python3 scripts/health.py digest --days 7 > "$WORK/digest.txt"
# Цели, «Мой день» (календарь), «Нагрузка и старты» и данные собирает scripts/context.py (секция health);
# секция без данных — нет сети, сломался календарь — просто выпадает, и заметка пишется без неё.
{
  cat health/PROMPT.md
  python3 scripts/context.py health
} > "$WORK/prompt.txt"

# Run from the work dir, outside the repo: no AGENTS.md, no project settings, no tools — the digest is the whole context.
cd "$WORK"
if ! python3 "$ROOT/scripts/codex_ask.py" --model "$MODEL" < prompt.txt > note.txt 2> codex.err; then
  echo "$STAMP codex failed:"; cat codex.err; exit 1
fi
cd "$ROOT"
python3 scripts/health.py review-save --model "$MODEL" --file "$WORK/note.txt" --digest-file "$WORK/digest.txt"
echo "$STAMP note ($MODEL):"; cat "$WORK/note.txt"; echo
