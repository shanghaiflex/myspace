#!/bin/sh
# Советы по фильмам, книгам и лекциям: дайджест вкуса → Codex → проверка в OMDb /
# Google Books / поиске YouTube → taste_recs.json → страницы «Фильмы», «Книги», «Лекции».
#   scripts/taste_recs.sh [film|book|lecture|all] [--force]   --force: не ждать сутки с прошлого подбора
# Запускается на mini из launchd раз в день (deploy/install-taste-recs.sh) и по кнопке
# на странице (POST /api/recs/<kind>/refresh). Нужен авторизованный `codex` (docs/ai.md).
# TASTE_RECS_MODEL переопределяет модель.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL=$(python3 "$ROOT/scripts/codex_ask.py" --resolve-model "${TASTE_RECS_MODEL:-}")
WORK="${TASTE_RECS_WORKDIR:-$HOME/.taste-recs}"; mkdir -p "$WORK"

# Разбираем аргументы без shift: `shift` при пустом списке возвращает ненулевой код,
# а с `set -e` это молча убивало запуск из launchd (без аргументов вовсе).
KINDS="film book lecture"
FORCE=""
for a in "$@"; do
  case "$a" in
    film|book|lecture) KINDS="$a" ;;
    both|all) KINDS="film book lecture" ;;
    --force) FORCE="--force" ;;
    *) echo "неизвестный аргумент: $a (film|book|lecture|all, --force)"; exit 2 ;;
  esac
done

command -v codex >/dev/null || { echo "codex not installed; see docs/ai.md"; exit 1; }

rc=0
for KIND in $KINDS; do
  STAMP=$(date '+%F %T')
  if [ "$FORCE" != "--force" ]; then
    python3 scripts/taste_recs.py due "$KIND" > "$WORK/due-$KIND.txt" \
      || { echo "$STAMP $KIND: пропускаю — $(cat "$WORK/due-$KIND.txt")"; continue; }
  fi

  python3 scripts/taste_recs.py digest "$KIND" > "$WORK/digest-$KIND.txt"
  case "$KIND" in
    film) PROMPT=films/PROMPT.md ;;
    book) PROMPT=books/PROMPT.md ;;
    lecture) PROMPT=lectures/PROMPT.md ;;
  esac
  { cat "$PROMPT"; printf '\n'; cat "$WORK/digest-$KIND.txt"; } > "$WORK/prompt-$KIND.txt"

  # Запуск из рабочего каталога, вне репозитория: ни AGENTS.md, ни настроек проекта, ни инструментов —
  # весь контекст модели это дайджест.
  cd "$WORK"
  if ! python3 "$ROOT/scripts/codex_ask.py" --model "$MODEL" --json \
        < "prompt-$KIND.txt" > "answer-$KIND.json" 2> "codex-$KIND.err"; then
    echo "$STAMP $KIND: codex не ответил:"; cat "codex-$KIND.err"; cd "$ROOT"; rc=1; continue
  fi
  cd "$ROOT"
  echo "$STAMP $KIND ($MODEL):"
  python3 scripts/taste_recs.py apply "$KIND" --file "$WORK/answer-$KIND.json" --model "$MODEL" || rc=1
done
exit $rc
