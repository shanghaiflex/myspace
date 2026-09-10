#!/bin/sh
# Один запуск агента: задача из agent_tasks.json → Claude Code с браузером → agent/reports/<id>/report.md
#   sh scripts/agent_run.sh <task-id>
# Дёргается из POST /api/agent (serve.py) в фоне. Нужен claude (CLAUDE_CODE_OAUTH_TOKEN в .env),
# node и @playwright/mcp (см. deploy/install-agent.sh). AGENT_MODEL переопределяет модель.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/node/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
ID="${1:?нужен id задачи}"
MODEL="${AGENT_MODEL:-opus}"
LIMIT="${AGENT_TIMEOUT:-1500}"          # 25 минут: дальше задача считается зависшей
PROFILE="${AGENT_BROWSER_PROFILE:-$HOME/.agent-browser}"
STAMP=$(date '+%F %T')

command -v claude >/dev/null || { python3 scripts/agent.py finish "$ID" --error "claude не установлен"; exit 1; }
command -v playwright-mcp >/dev/null || { python3 scripts/agent.py finish "$ID" --error "playwright-mcp не установлен (deploy/install-agent.sh)"; exit 1; }

DIR=$(python3 scripts/agent.py start "$ID")
mkdir -p "$DIR" "$PROFILE"

# Конфиг MCP пишем в каталог задачи: браузер складывает скриншоты туда же, где отчёт.
cat > "$DIR/mcp.json" <<JSON
{"mcpServers":{"playwright":{"command":"playwright-mcp","args":[
  "--headless","--user-data-dir","$PROFILE","--output-dir","$DIR",
  "--viewport-size","1280x900","--timeout-navigation","60000","--image-responses","omit"]}}}
JSON

python3 - "$ID" > "$DIR/task.txt" <<'PY'
import json, os, sys
root = os.getcwd()
tasks = json.load(open(os.path.join(root, "agent_tasks.json"), encoding="utf-8"))["tasks"]
t = next(x for x in tasks if x["id"] == sys.argv[1])
print(open(os.path.join(root, "agent", "PROMPT.md"), encoding="utf-8").read())
print("\n## Задача\n")
print(t["prompt"])
PY

# Работаем из каталога задачи: файловые инструменты заперты в нём, Bash в --restricted нет вовсе,
# настройки и CLAUDE.md репозитория не подхватываются — у агента только браузер и этот каталог.
cd "$DIR"
: > claude.err
claude -p --model "$MODEL" \
  --restricted \
  --tools "Read,Write,Edit,WebFetch,WebSearch,TodoWrite" \
  --mcp-config mcp.json --strict-mcp-config \
  --allowed-tools "mcp__playwright" \
  --permission-mode bypassPermissions \
  --no-session-persistence --output-format json \
  < task.txt > result.json 2> claude.err &
PID=$!
( sleep "$LIMIT"; kill "$PID" 2>/dev/null ) & WATCH=$!
RC=0; wait "$PID" || RC=$?
kill "$WATCH" 2>/dev/null || true
cd "$ROOT"

COST=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("total_cost_usd") or "")' "$DIR/result.json" 2>/dev/null || true)
TURNS=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("num_turns") or "")' "$DIR/result.json" 2>/dev/null || true)
SET_COST=""; [ -n "$COST" ] && SET_COST="--cost $COST"
SET_TURNS=""; [ -n "$TURNS" ] && SET_TURNS="--turns $TURNS"

if [ "$RC" != 0 ] && [ ! -s "$DIR/report.md" ]; then
  ERR=$(tail -c 400 "$DIR/claude.err" 2>/dev/null)
  [ -n "$ERR" ] || ERR="не уложился за $((LIMIT / 60)) мин"
  python3 scripts/agent.py finish "$ID" --error "$ERR" $SET_COST $SET_TURNS
  echo "$STAMP [$ID] ошибка: $ERR"
  exit 1
fi
python3 scripts/agent.py finish "$ID" --report "$DIR/report.md" $SET_COST $SET_TURNS
echo "$STAMP [$ID] готово (${TURNS:-?} шагов, \$${COST:-?})"
