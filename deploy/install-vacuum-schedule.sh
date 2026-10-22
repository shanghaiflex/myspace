#!/bin/sh
# Ежедневная уборка и мониторинг ошибок робота/станции (scripts/vacuum_schedule.py).
# Запускать НА MINI после деплоя: sh ~/movies/deploy/install-vacuum-schedule.sh
# Агент проверяет ошибки каждую минуту; в 10:30 МСК запускает уборку.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.vacuum
PY="$HOME/.local/python312/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
PATHV="$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
    <string>set -a; [ -f ./.env ] &amp;&amp; . ./.env; set +a; exec "$PY" scripts/vacuum_schedule.py tick</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>60</integer>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/vacuum-schedule.log</string><key>StandardErrorPath</key><string>$ROOT/logs/vacuum-schedule.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
sh "$ROOT/deploy/claim-log.sh" "$ROOT/logs/vacuum-schedule.log"
echo "$LABEL installed: каждую минуту, лог $ROOT/logs/vacuum-schedule.log"
"$PY" "$ROOT/scripts/vacuum_schedule.py" show
