#!/bin/sh
# Installs the daily race advice as a launchd agent on the machine that runs the site (the mini).
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-races.sh
# Needs `claude` in ~/.local/bin, CLAUDE_CODE_OAUTH_TOKEN in .env.
# RACES_HOUR / RACES_MINUTE change the time of day (default 08:20, после утренних советов).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.races
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/races.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>${RACES_HOUR:-8}</integer><key>Minute</key><integer>${RACES_MINUTE:-20}</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/races.log</string><key>StandardErrorPath</key><string>$ROOT/logs/races.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: daily at ${RACES_HOUR:-8}:$(printf '%02d' "${RACES_MINUTE:-20}"), log $ROOT/logs/races.log"
