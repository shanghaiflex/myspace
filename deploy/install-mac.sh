#!/bin/sh
# Runs serve.py and the git sync as launchd agents on a Mac (e.g. the Mac mini). Re-run after moving the repo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
PY="$(command -v python3)"; PATHV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f "$ROOT/.env" ] || { echo "create $ROOT/.env with MOVIES_PASSWORD=... first (see .env.example)"; exit 1; }
cat > "$AG/cc.bodywithoutorgans.serve.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cc.bodywithoutorgans.serve</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/serve.py</string><string>8787</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/serve.log</string><key>StandardErrorPath</key><string>$ROOT/logs/serve.log</string>
</dict></plist>
PL
cat > "$AG/cc.bodywithoutorgans.sync.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cc.bodywithoutorgans.sync</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/sync.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string></dict>
  <key>StartInterval</key><integer>300</integer><key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/sync.log</string><key>StandardErrorPath</key><string>$ROOT/logs/sync.log</string>
</dict></plist>
PL
for l in serve sync; do launchctl unload "$AG/cc.bodywithoutorgans.$l.plist" 2>/dev/null || true; launchctl load "$AG/cc.bodywithoutorgans.$l.plist"; done
sleep 1; curl -s http://localhost:8787/api/ping && echo " ← serve.py is up"
