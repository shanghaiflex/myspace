#!/bin/sh
# Installs the daily food note as a launchd agent on the machine that runs the site (the mini).
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-pantry.sh
# Needs, on that machine:
#   - `claude` in ~/.local/bin and CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env
#   - the Gmail app password in the login keychain:
#       security add-generic-password -a s.s.filatov94@gmail.com -s gmail-imap-mcp -w
#     (launchd agents run in the user's session, so the login keychain is reachable;
#      after a reboot the keychain must be unlocked, which auto-login does.)
# PANTRY_INTERVAL overrides the period in seconds (default 86400 = once a day).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.pantry
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/pantry_review.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/logs/pantry.log</string><key>StandardErrorPath</key><string>$ROOT/logs/pantry.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: daily at 09:30, log $ROOT/logs/pantry.log"
echo "check now: sh $ROOT/scripts/pantry_review.sh --force"
