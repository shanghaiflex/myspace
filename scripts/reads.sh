#!/bin/sh
# Советы по статьям: свежие ленты → дайджест вкуса по всем каталогам → Codex → reads.json.
#   scripts/reads.sh [--force]      --force: не ждать сутки с прошлого подбора
# Запускается на mini из launchd раз в день (deploy/install-reads.sh) и по кнопке (POST /api/reads/refresh).
# Нужен авторизованный `codex` (docs/ai.md). READS_MODEL переопределяет модель.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL=$(python3 "$ROOT/scripts/codex_ask.py" --resolve-model "${READS_MODEL:-}")
WORK="${READS_WORKDIR:-$HOME/.reads}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

if [ "${1:-}" != "--force" ]; then
  python3 scripts/reads.py due > "$WORK/due.txt" || { echo "$STAMP пропускаю: $(cat "$WORK/due.txt")"; exit 0; }
fi
command -v codex >/dev/null || { echo "codex not installed; see docs/ai.md"; exit 1; }

echo "$STAMP ленты:"
python3 scripts/reads.py fetch
python3 scripts/reads.py digest > "$WORK/digest.txt"
{ cat reads/PROMPT.md; printf '\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"

# Из рабочего каталога, вне репозитория: у модели нет ни инструментов, ни настроек проекта — только дайджест.
cd "$WORK"
if ! python3 "$ROOT/scripts/codex_ask.py" --model "$MODEL" --json < prompt.txt > answer.json 2> codex.err; then
  echo "$STAMP codex не ответил:"; cat codex.err; exit 1
fi
cd "$ROOT"
echo "$STAMP статьи ($MODEL):"
python3 scripts/reads.py apply --file "$WORK/answer.json" --model "$MODEL"
