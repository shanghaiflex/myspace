#!/bin/sh
# Самопроверка BoW (scripts/selfcheck.py) как launchd-агент на mini: раз в 15 минут, молчит, когда всё хорошо.
# Run ON THE MINI after deploy: sh ~/movies/deploy/install-selfcheck.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.selfcheck
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$HOME/.local/python312/bin/python3</string><string>$ROOT/scripts/selfcheck.py</string><string>run</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>${SELFCHECK_INTERVAL:-900}</integer><key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/selfcheck.log</string><key>StandardErrorPath</key><string>$ROOT/logs/selfcheck.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
sh "$ROOT/deploy/claim-log.sh" "$ROOT/logs/selfcheck.log"
echo "$LABEL installed: every ${SELFCHECK_INTERVAL:-900}s, log $ROOT/logs/selfcheck.log, state $ROOT/data/selfcheck.json"
