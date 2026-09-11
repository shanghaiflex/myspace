#!/bin/sh
# Read-only диагностика домашнего VPN на mini: состояние интерфейса, рукопожатие
# AmneziaWG (сокет в /var/run доступен только root) и хвост лога демона.
# Ставится в /usr/local/sbin от root (deploy/install-vpn-sudoers.sh), вызывается без пароля:
#   ssh mini 'sudo -n /usr/local/sbin/vpn-status.sh'
set -u
IFACE=$(cat /var/run/amneziawg/mini.iface 2>/dev/null || echo utun11)
echo "iface: $IFACE"
ifconfig "$IFACE" 2>/dev/null | sed -n '1,2p'
echo "маршрут по умолчанию: $(route -n get 1.1.1.1 2>/dev/null | awk '/interface:/{print $2}')"

SOCK="/var/run/amneziawg/$IFACE.sock"
if [ -S "$SOCK" ]; then
  printf 'get=1\n\n' | nc -U "$SOCK" 2>/dev/null | awk -F= -v now="$(date +%s)" '
    /^last_handshake_time_sec=/ { hs = $2 }
    /^rx_bytes=/ { rx = $2 }
    /^tx_bytes=/ { tx = $2 }
    /^endpoint=/ { ep = $2 }
    END {
      if (hs == "" || hs == 0) print "рукопожатие: НЕТ"
      else printf "рукопожатие: %d с назад\n", now - hs
      printf "endpoint: %s\nrx: %s  tx: %s\n", ep, rx, tx
    }'
else
  echo "сокета $SOCK нет — тоннель не поднят"
fi

echo "--- /var/log/awg-mini.log:"
tail -5 /var/log/awg-mini.log 2>/dev/null
