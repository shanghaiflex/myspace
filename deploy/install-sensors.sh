#!/bin/sh
# Логирование датчиков температуры и влажности в health.db (launchd на mini — только там есть MQTT).
# Запускать НА MINI после деплоя: sh ~/movies/deploy/install-sensors.sh
# Раз в 10 минут снимает retained-отчёт датчиков; повторы отсеиваются по last_seen, см. scripts/sensors.py.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.sensors
PY="$HOME/.local/python312/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
PATHV="$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/scripts/sensors.py</string><string>log</string><string>--quiet</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/sensors.log</string><key>StandardErrorPath</key><string>$ROOT/logs/sensors.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: каждые 10 минут, лог $ROOT/logs/sensors.log"
