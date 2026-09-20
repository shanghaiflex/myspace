#!/bin/sh
# Итоги недели: дайджест по всем данным сайта (scripts/week.py) → Claude Code (Opus) → health.db (reviews, kind=week)
# → главная («Итоги недели») и уведомление на телефон. Запускается на mini по воскресеньям в 08:40
# (deploy/install-week-review.sh) и по POST /api/health/week.
#   scripts/week_review.sh
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${HEALTH_MODEL:-opus}"
WORK="${WEEK_WORKDIR:-$HOME/.week-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')
command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }

python3 scripts/week.py digest > "$WORK/digest.txt"
GOALS=$(grep -v '^\s*#' health/goals.md 2>/dev/null | grep -v '^\s*$' || true)
{
  cat health/WEEK.md
  printf '\n## Мои цели\n%s\n' "${GOALS:-(целей нет)}"
  printf '\n## Данные\n'
  cat "$WORK/digest.txt"
} > "$WORK/prompt.txt"

# Из рабочего каталога, вне репозитория: ни CLAUDE.md, ни инструментов — только дайджест.
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > note.txt 2> claude.err; then
  echo "$STAMP claude failed:"; cat claude.err; exit 1
fi
cd "$ROOT"
python3 scripts/health.py review-save --kind week --model "$MODEL" --file "$WORK/note.txt" --digest-file "$WORK/digest.txt"
echo "$STAMP week ($MODEL):"; cat "$WORK/note.txt"; echo
