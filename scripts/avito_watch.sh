#!/bin/sh
# Прогон слежки за Авито (scripts/avito_watch.py run) — на mini из launchd (deploy/install-avito-watch.sh).
#   scripts/avito_watch.sh [--force] [--watch id] [--max-cards N]
# Python — отдельный venv с playwright (~/.local/avito-venv), браузер — видимое окно Chrome for Testing.
# Нужен `claude` и CLAUDE_CODE_OAUTH_TOKEN в .env: правила и фото проверяет claude -p.
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PY="${AVITO_PY:-$HOME/.local/avito-venv/bin/python}"
[ -x "$PY" ] || PY=python3
exec "$PY" scripts/avito_watch.py run "$@"
