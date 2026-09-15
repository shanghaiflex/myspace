#!/bin/sh
# Ставит сторожа пути наружу (VPN → Cloudflare → тоннель → serve) как launchd-агент на mini.
# Запускать НА MINI после деплоя: sh ~/movies/deploy/install-vpn-watchdog.sh
# Нужен уже установленный deploy/install-vpn-sudoers.sh: сторож перезапускает VPN без пароля.
# WATCHDOG_INTERVAL меняет период проверки (по умолчанию 120 с).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$ROOT/logs"
LABEL=cc.bodywithoutorgans.vpnwatchdog
PATHV="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cat > "$AG/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$ROOT/scripts/vpn_watchdog.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StartInterval</key><integer>${WATCHDOG_INTERVAL:-120}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/vpn-watchdog.log</string><key>StandardErrorPath</key><string>$ROOT/logs/vpn-watchdog.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AG/$LABEL.plist"
echo "$LABEL installed: каждые ${WATCHDOG_INTERVAL:-120} с, лог $ROOT/logs/vpn-watchdog.log"
echo "проверка права на перезапуск VPN без пароля:"
sudo -n /usr/local/sbin/vpn-status.sh >/dev/null 2>&1 \
  && echo "  ok — sudoers на месте" \
  || echo "  НЕТ: сначала ssh -t mini 'sudo sh movies/deploy/install-vpn-sudoers.sh'"
