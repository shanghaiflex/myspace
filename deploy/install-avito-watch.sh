#!/bin/sh
# Слежка за Авито на mini: venv с playwright, av.py скилла avito и launchd-агент.
# Запускать С НОУТБУКА (нужен ~/.claude/skills/avito/av.py): sh deploy/install-avito-watch.sh
# Агент cc.bodywithoutorgans.avitowatch стартует в 06:30, 10:30, 14:30 и 18:30, но работает один раз в день:
# поздние запуски — только если утренний упёрся в блок, капчу или оставил очередь (avito_watch.py due).
set -e
HOST="${MINI_HOST:-mini}"
cd "$(dirname "$0")/.."
ssh "$HOST" 'mkdir -p ~/.claude/skills/avito ~/movies/avito/watches ~/movies/scripts ~/movies/logs ~/.avito-watch'
scp -q ~/.claude/skills/avito/av.py "$HOST:.claude/skills/avito/av.py"
rsync -az --exclude watches avito/ "$HOST:movies/avito/"   # слежки живут только на mini
scp -q scripts/avito_watch.py scripts/avito_watch.sh "$HOST:movies/scripts/"
ssh "$HOST" 'sh -s' <<'REMOTE'
set -e
V="$HOME/.local/avito-venv"
if ! "$V/bin/python" -c "import playwright" 2>/dev/null; then
  ~/.local/bin/uv venv -q --python ~/.local/python312/bin/python3 "$V"
  ~/.local/bin/uv pip install -q --python "$V/bin/python" playwright==1.63.0
fi
ROOT="$HOME/movies"; AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG"
LABEL=cc.bodywithoutorgans.avitowatch
slots=""
for h in 6 10 14 18; do slots="$slots<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>30</integer></dict>"; done
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/avito_watch.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>$HOME</string></dict>
  <key>StartCalendarInterval</key><array>$slots</array>
  <key>StandardOutPath</key><string>$ROOT/logs/avito-watch.log</string><key>StandardErrorPath</key><string>$ROOT/logs/avito-watch.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
# Без RunAtLoad лог первым создаёт ручной прогон по ssh, и launchd его не откроет (exit 78): см. claim-log.sh.
# Ручной прогон поэтому — без `>> logs/avito-watch.log`, иначе лог снова станет чужим.
sh "$ROOT/deploy/claim-log.sh" "$ROOT/logs/avito-watch.log"
echo "$LABEL installed: 06:30/10:30/14:30/18:30 (работает раз в день), log $ROOT/logs/avito-watch.log"
REMOTE
