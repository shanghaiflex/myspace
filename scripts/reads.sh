#!/bin/sh
# Советы по статьям: свежие ленты → дайджест вкуса по всем каталогам → Claude Code (Opus) → reads.json.
#   scripts/reads.sh [--force]      --force: не ждать сутки с прошлого подбора
# Запускается на mini из launchd раз в день (deploy/install-reads.sh) и по кнопке (POST /api/reads/refresh).
# Нужен `claude` и CLAUDE_CODE_OAUTH_TOKEN или ANTHROPIC_API_KEY в .env. READS_MODEL переопределяет модель.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${READS_MODEL:-opus}"
WORK="${READS_WORKDIR:-$HOME/.reads}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

if [ "${1:-}" != "--force" ]; then
  python3 scripts/reads.py due > "$WORK/due.txt" || { echo "$STAMP пропускаю: $(cat "$WORK/due.txt")"; exit 0; }
fi
command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }

echo "$STAMP ленты:"
python3 scripts/reads.py fetch
python3 scripts/reads.py digest > "$WORK/digest.txt"
{ cat reads/PROMPT.md; printf '\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"

# Из рабочего каталога, вне репозитория: у модели нет ни инструментов, ни настроек проекта — только дайджест.
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > answer.json 2> claude.err; then
  echo "$STAMP claude не ответил:"; cat claude.err; exit 1
fi
cd "$ROOT"
echo "$STAMP статьи ($MODEL):"
python3 scripts/reads.py apply --file "$WORK/answer.json" --model "$MODEL"
