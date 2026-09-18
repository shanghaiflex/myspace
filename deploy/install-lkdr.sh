#!/bin/sh
# Ежедневная синхронизация чеков ФНС на mini (launchd). Запускать НА MINI после деплоя:
#   sh ~/movies/deploy/install-lkdr.sh
# Ключ (data/lkdr-auth.json) намеренно не ездит через rsync — перенести его с ноутбука отдельно:
#   scp data/lkdr-auth.json data/lkdr-receipts.json mini:movies/data/
# Синхронизация идёт в 09:15, перед пересчётом еды в 09:30 — тот читает те же чеки.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.lkdr
PATHV="$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f "$ROOT/data/lkdr-auth.json" ] || echo "ВНИМАНИЕ: нет $ROOT/data/lkdr-auth.json — синк будет падать, пока не перенесёшь ключ"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/lkdr_sync.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/lkdr.log</string><key>StandardErrorPath</key><string>$ROOT/logs/lkdr.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: ежедневно в 09:15, лог $ROOT/logs/lkdr.log"
