#!/bin/sh
# Ставит split tunneling на mini: сети из deploy/direct-routes.txt пойдут мимо VPN.
# Запускать на mini от root ОДИН раз:
#   ssh -t mini 'sudo sh movies/deploy/install-direct-routes.sh'
# Дальше список правится в репозитории, деплой копирует его, а обновить маршруты:
#   ssh mini 'sudo launchctl kickstart -k system/cc.bodywithoutorgans.directroutes'
set -e
[ "$(id -u)" = 0 ] || { echo "нужен root: sudo sh $0"; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
LABEL=cc.bodywithoutorgans.directroutes
PLIST=/Library/LaunchDaemons/$LABEL.plist
OWNER=$(stat -f %Su "$SRC")
LOG=$(dirname "$SRC")/logs/direct-routes.log

install -d -m 755 /usr/local/sbin /usr/local/etc
install -m 755 "$SRC/direct-routes.sh" /usr/local/sbin/direct-routes.sh
install -m 644 "$SRC/direct-routes.txt" /usr/local/etc/direct-routes.txt
install -d -m 755 -o "$OWNER" "$(dirname "$LOG")" 2>/dev/null || true

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>/usr/local/sbin/direct-routes.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL
chmod 644 "$PLIST"
launchctl bootout system/$LABEL 2>/dev/null || true
launchctl bootstrap system "$PLIST"
launchctl kickstart -k system/$LABEL
sleep 2
echo "--- $LOG:"; tail -3 "$LOG" 2>/dev/null || true
