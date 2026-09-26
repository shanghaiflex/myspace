#!/bin/sh
# Hourly health note: digest of health.db → Claude Code (Opus) → short note saved back to health.db and shown on the home page.
#   scripts/health_review.sh [--force]      --force: run even if no new samples arrived since the last note
# Runs on the mini from launchd (deploy/install-health-review.sh, every hour) and from POST /api/health/review.
# Needs `claude` (Claude Code CLI) and either CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token` on the laptop) or
# ANTHROPIC_API_KEY in .env. HEALTH_MODEL overrides the model (default: opus = latest Opus).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${HEALTH_MODEL:-opus}"
WORK="${HEALTH_WORKDIR:-$HOME/.health-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

if [ "$1" != "--force" ]; then
  # Не «есть ли новые образцы», а «изменилось ли что-то существенное» — см. health.changed().
  python3 scripts/health.py pending > "$WORK/pending.json" || { echo "$STAMP skip: $(cat "$WORK/pending.json")"; exit 0; }
  echo "$STAMP why: $(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['why'])" "$WORK/pending.json")"
fi
command -v claude >/dev/null || { echo "$STAMP claude not installed (curl -fsSL https://claude.ai/install.sh | bash)"; exit 1; }

python3 scripts/health.py digest --days 7 > "$WORK/digest.txt"
GOALS=$(grep -v '^\s*#' health/goals.md 2>/dev/null | grep -v '^\s*$' || true)
# The calendar is what the body data cannot say: whether there is room in the day and how early tomorrow starts.
# It is optional — no link, no network, a broken export, and the note is simply written without it, as before.
SCHEDULE=$(python3 scripts/schedule.py digest 2>/dev/null || true)
# Load over weeks, not one day, plus races ahead with the weather at the start (scripts/load.py). Optional as well.
LOAD=$(python3 scripts/load.py digest 2>/dev/null || true)
{
  cat health/PROMPT.md
  printf '\n## Мои цели\n%s\n' "${GOALS:-(целей пока нет — ориентируйся на мои средние и общие нормы)}"
  [ -n "$SCHEDULE" ] && printf '\n## Мой день\n%s\n' "$SCHEDULE"
  [ -n "$LOAD" ] && printf '\n## Нагрузка и старты\n%s\n' "$LOAD"
  printf '\n## Данные\n'
  cat "$WORK/digest.txt"
} > "$WORK/prompt.txt"

# Run from the work dir, outside the repo: no CLAUDE.md, no project settings, no tools — the digest is the whole context.
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > note.txt 2> claude.err; then
  echo "$STAMP claude failed:"; cat claude.err; exit 1
fi
cd "$ROOT"
python3 scripts/health.py review-save --model "$MODEL" --file "$WORK/note.txt" --digest-file "$WORK/digest.txt"
echo "$STAMP note ($MODEL):"; cat "$WORK/note.txt"; echo
