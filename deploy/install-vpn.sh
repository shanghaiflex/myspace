#!/bin/sh
# Ставит (или меняет) VPN на mini: демон cc.bodywithoutorgans.vpn → deploy/awg-mini.sh поверх
# конфига AmneziaWG. Сервер задаётся только конфигом — Amnezia Premium, свой VPS, что угодно,
# что AmneziaVPN умеет выгрузить «родным» .conf.
#
# Конфиг — СВОЙ для mini: один и тот же пир на двух устройствах дерётся (так было 07.09 с
# Server 1 — 25–40% потерь). Для Premium: AmneziaVPN на ноутбуке → сервер Premium → настройки →
# конфигурационные файлы → выбрать страну → сохранить .conf; скопировать на mini:
#   scp mini.conf mini:/tmp/awg-premium.conf
# и запустить от root:
#   ssh -t mini 'sudo sh movies/deploy/install-vpn.sh /tmp/awg-premium.conf'
# Без аргумента — только переставить скрипт и перезапустить с тем конфигом, что уже лежит.
set -e
[ "$(id -u)" = 0 ] || { echo "нужен root: sudo sh $0 [конфиг.conf]"; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
CONF=/usr/local/etc/amneziawg/mini.conf
PLIST=/Library/LaunchDaemons/cc.bodywithoutorgans.vpn.plist
STAMP=$(date +%Y%m%d-%H%M%S)

if [ -n "${1:-}" ]; then
  grep -q '^\[Peer\]' "$1" && grep -qi '^PrivateKey' "$1" || { echo "$1 не похож на конфиг AmneziaWG"; exit 1; }
  install -d -m 700 /usr/local/etc/amneziawg
  [ -f "$CONF" ] && cp -p "$CONF" "$CONF.bak-$STAMP"
  install -o root -g wheel -m 600 "$1" "$CONF"
  rm -f "$1"   # ключ не должен оставаться в /tmp
  echo "конфиг: $CONF ($(awk -F'= *' 'tolower($1) ~ /^endpoint/{print $2}' "$CONF"))"
fi
[ -f "$CONF" ] || { echo "нет $CONF — передай конфиг аргументом"; exit 1; }

# прежний скрипт (с зашитым Server 1) остаётся рядом — откат одной командой cp
[ -f /usr/local/sbin/awg-mini.sh ] && ! cmp -s "$SRC/awg-mini.sh" /usr/local/sbin/awg-mini.sh \
  && cp -p /usr/local/sbin/awg-mini.sh "/usr/local/sbin/awg-mini.sh.bak-$STAMP"
install -o root -g wheel -m 700 "$SRC/awg-mini.sh" /usr/local/sbin/awg-mini.sh
install -o root -g wheel -m 755 "$SRC/vpn-status.sh" /usr/local/sbin/vpn-status.sh
# прямые маршруты (ФНС, Яндекс, …) лежат у root — ставятся здесь же, чтобы смена VPN была одной командой
install -m 755 "$SRC/direct-routes.sh" /usr/local/sbin/direct-routes.sh
install -m 644 "$SRC/direct-routes.txt" /usr/local/etc/direct-routes.txt

if [ ! -f "$PLIST" ]; then
  cat > "$PLIST" <<'P'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cc.bodywithoutorgans.vpn</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>/usr/local/sbin/awg-mini.sh</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>/var/log/awg-mini.log</string><key>StandardErrorPath</key><string>/var/log/awg-mini.log</string>
</dict></plist>
P
  chmod 644 "$PLIST"
  launchctl bootstrap system "$PLIST"
else
  launchctl kickstart -k system/cc.bodywithoutorgans.vpn
fi

launchctl kickstart -k system/cc.bodywithoutorgans.directroutes 2>/dev/null || true
echo "жду рукопожатия…"
for i in $(seq 1 30); do
  sleep 1
  /usr/local/sbin/vpn-status.sh | grep -q 'рукопожатие: [0-9]' && { echo "рукопожатие через $i с"; break; }
done
/usr/local/sbin/vpn-status.sh
sleep 3   # первые секунды после рукопожатия пакеты ещё теряются
# ipinfo на общем IP Premium отвечает 429, а ICMP сервер не пропускает — проверка обычным HTTPS
echo "--- внешний IP: $(curl -s -m 15 https://api.ipify.org || echo 'наружу не выходит')"
echo "--- lkdr.nalog.ru мимо VPN: $(curl -s -m 15 -o /dev/null -w '%{http_code}' https://lkdr.nalog.ru/)"
