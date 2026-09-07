#!/bin/sh
# Installs the daily mix advice as a launchd agent on the machine that runs the site (the mini).
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-mix-recs.sh
# Needs `claude` in ~/.local/bin, CLAUDE_CODE_OAUTH_TOKEN in .env and yt-dlp in ~/bin (SoundCloud client_id).
# MIX_RECS_HOUR / MIX_RECS_MINUTE change the time of day (default 07:20, before the morning coffee).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.mixrecs
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/mix_recs.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>${MIX_RECS_HOUR:-7}</integer><key>Minute</key><integer>${MIX_RECS_MINUTE:-20}</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/mix-recs.log</string><key>StandardErrorPath</key><string>$ROOT/logs/mix-recs.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: daily at ${MIX_RECS_HOUR:-7}:$(printf '%02d' "${MIX_RECS_MINUTE:-20}"), log $ROOT/logs/mix-recs.log"
