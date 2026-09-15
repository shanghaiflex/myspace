#!/bin/sh
# Французский: кандидаты (ленты + поиск YouTube) → Claude выбирает материалы → скрипт качает
# настоящий текст (субтитры ролика / текст статьи) → Claude собирает по нему слова и тест → french.json.
#   scripts/french.sh [--force] [--lessons]
#     --force    не ждать сутки с прошлого подбора
#     --lessons  только второй проход: досбор разборов к уже выбранным материалам
# Запускается на mini из launchd (deploy/install-french.sh) и со страницы (POST /api/french/refresh);
# serve.py дёргает его сам, когда я закончил последний материал.
# Нужен `claude` и CLAUDE_CODE_OAUTH_TOKEN или ANTHROPIC_API_KEY в .env. FRENCH_MODEL меняет модель.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL="${FRENCH_MODEL:-opus}"
WORK="${FRENCH_WORKDIR:-$HOME/.french}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

FORCE=""; ONLY_LESSONS=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --lessons) ONLY_LESSONS=1 ;;
    *) echo "неизвестный аргумент: $a (--force, --lessons)"; exit 2 ;;
  esac
done

command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }

ask() {  # ask <prompt-file> <out-file>; запуск вне репозитория: ни CLAUDE.md, ни инструментов
  cd "$WORK"
  claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < "$1" > "$2" 2> "claude.err" \
    || { echo "$STAMP claude не ответил:"; cat claude.err; cd "$ROOT"; return 1; }
  cd "$ROOT"
}

if [ -z "$ONLY_LESSONS" ]; then
  if [ -z "$FORCE" ]; then
    python3 scripts/french.py due > "$WORK/due.txt" || { echo "$STAMP пропускаю: $(cat "$WORK/due.txt")"; exit 0; }
  fi
  echo "$STAMP кандидаты:"
  python3 scripts/french.py fetch
  # Календарь — не для содержания, а для размера: в плотный день материал должен быть короче.
  # Нет сети или сломался календарь — подбор идёт как раньше, просто без «Моего дня».
  SCHEDULE=$(python3 scripts/schedule.py digest 2>/dev/null || true)
  {
    cat french/PROMPT.md
    [ -n "$SCHEDULE" ] && printf '\n## Мой день\n%s\n' "$SCHEDULE"
    printf '\n'
    python3 scripts/french.py digest
  } > "$WORK/prompt.txt"
  ask "$WORK/prompt.txt" "$WORK/answer.json" || exit 1
  echo "$STAMP материалы ($MODEL):"
  python3 scripts/french.py apply --file "$WORK/answer.json" --model "$MODEL"
fi

# Второй проход: разбор по настоящему тексту. Если разбирать нечего — это не ошибка.
if python3 scripts/french.py lesson-digest > "$WORK/texts.txt" 2>"$WORK/texts.err"; then
  { cat french/LESSON.md; printf '\n'; cat "$WORK/texts.txt"; } > "$WORK/lesson-prompt.txt"
  ask "$WORK/lesson-prompt.txt" "$WORK/lesson.json" || exit 1
  echo "$STAMP разборы ($MODEL):"
  python3 scripts/french.py lessons --file "$WORK/lesson.json" --model "$MODEL"
else
  echo "$STAMP разбор не нужен: $(cat "$WORK/texts.err")"
fi
python3 scripts/french.py stats
