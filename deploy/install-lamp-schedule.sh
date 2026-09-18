#!/bin/sh
# Вечерний свет по закату вместо трёх жёстких времён в launchd.
# Запускать НА MINI после деплоя: sh ~/movies/deploy/install-lamp-schedule.sh
#
# Ставит один агент, который каждые 5 минут смотрит, в какой фазе дня он сейчас (scripts/lamp_schedule.py),
# и уносит прежние — local.lamp.evening / nightlight / night / catchup и local.plug.evening / night —
# в ~/Library/LaunchAgents/disabled-by-lamp-schedule (не удаляет: вернуть можно руками).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
OLD="$AG/disabled-by-lamp-schedule"
LABEL=cc.bodywithoutorgans.lampschedule
PY="$HOME/.local/python312/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
PATHV="$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

for L in local.lamp.evening local.lamp.nightlight local.lamp.night local.lamp.catchup local.plug.evening local.plug.night; do
  [ -f "$AG/$L.plist" ] || continue
  launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
  mkdir -p "$OLD"; mv "$AG/$L.plist" "$OLD/$L.plist"
  echo "убран старый агент $L"
done

cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/scripts/lamp_schedule.py</string><string>run</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/lamp-schedule.log</string><key>StandardErrorPath</key><string>$ROOT/logs/lamp-schedule.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: каждые 5 минут, лог $ROOT/logs/lamp-schedule.log"
"$PY" "$ROOT/scripts/lamp_schedule.py" show
