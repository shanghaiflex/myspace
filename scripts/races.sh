#!/bin/sh
# Советы по стартам: календари серий → разбор изменившихся страниц (Claude вынимает старты с цитатой, скрипт
# проверяет цитату) → дайджест меня и кандидатов → Claude выбирает номера → races.json.
#   scripts/races.sh [--force]      --force: не ждать суток с прошлого подбора и перечитать страницы
# Запускается на mini из launchd раз в день (deploy/install-races.sh) и по кнопке (POST /api/recs/race/refresh).
# Нужен `claude` и CLAUDE_CODE_OAUTH_TOKEN в .env. RACES_MODEL переопределяет модель выбора.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${RACES_MODEL:-opus}"
WORK="${RACES_WORKDIR:-$HOME/.races}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')
FORCE=""; [ "${1:-}" = "--force" ] && FORCE="--force"

if [ -z "$FORCE" ]; then
  python3 scripts/races.py due > "$WORK/due.txt" || { echo "$STAMP пропускаю: $(cat "$WORK/due.txt")"; exit 0; }
fi
command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }

echo "$STAMP календари серий:"
python3 scripts/races.py fetch $FORCE
python3 scripts/races.py extract-prompt > "$WORK/pages.txt"
if [ -s "$WORK/pages.txt" ]; then
  { cat races/EXTRACT.md; printf '\nСегодня %s.\n' "$(date +%F)"; cat "$WORK/pages.txt"; } > "$WORK/extract.txt"
  # Разбор — работа по тексту, а не вкус: хватает sonnet. Инструментов у модели нет.
  (cd "$WORK" && claude -p --model "${RACES_EXTRACT_MODEL:-sonnet}" --tools "" --no-session-persistence \
      --output-format text < extract.txt > events.json 2> claude.err) || { echo "$STAMP разбор не удался:"; cat "$WORK/claude.err"; exit 1; }
  echo "$STAMP разбор:"
  python3 scripts/races.py events --file "$WORK/events.json"
else
  echo "$STAMP страницы не менялись — разбирать нечего"
fi

python3 scripts/races.py digest > "$WORK/digest.txt"
{ cat races/PROMPT.md; printf '\n'; cat "$WORK/digest.txt"; } > "$WORK/prompt.txt"
cd "$WORK"
if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < prompt.txt > answer.json 2> claude.err; then
  echo "$STAMP claude не ответил:"; cat claude.err; exit 1
fi
cd "$ROOT"
echo "$STAMP старты ($MODEL):"
python3 scripts/races.py apply --file "$WORK/answer.json" --model "$MODEL"
