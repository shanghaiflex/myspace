#!/bin/sh
# Installs the hourly health note as a launchd agent on the machine that runs the site (the mini).
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-health-review.sh
# Needs `claude` in ~/.local/bin (curl -fsSL https://claude.ai/install.sh | bash) and CLAUDE_CODE_OAUTH_TOKEN in .env.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.health
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/health_review.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>${HEALTH_INTERVAL:-3600}</integer><key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/health-review.log</string><key>StandardErrorPath</key><string>$ROOT/logs/health-review.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: every ${HEALTH_INTERVAL:-3600}s, log $ROOT/logs/health-review.log"
