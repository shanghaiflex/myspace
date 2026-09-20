#!/bin/sh
# Итоги недели как launchd-агент на машине с сайтом (mini): по воскресеньям в 08:40, после всех утренних советов.
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-week-review.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.week
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/week_review.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>${WEEK_HOUR:-8}</integer><key>Minute</key><integer>${WEEK_MINUTE:-40}</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/week-review.log</string><key>StandardErrorPath</key><string>$ROOT/logs/week-review.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: Sundays at ${WEEK_HOUR:-8}:$(printf '%02d' "${WEEK_MINUTE:-40}"), log $ROOT/logs/week-review.log"
