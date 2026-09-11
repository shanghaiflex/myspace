#!/bin/sh
# Разрешает пользователю mini перезапускать VPN и смотреть его состояние без пароля.
# Список команд закрытый: только launchctl по двум нашим демонам и read-only vpn-status.sh.
# Запускать на mini от root ОДИН раз:
#   ssh -t mini 'sudo sh movies/deploy/install-vpn-sudoers.sh'
# Дальше (без пароля, в том числе из Claude Code с ноутбука):
#   ssh mini 'sudo -n launchctl kickstart -k system/cc.bodywithoutorgans.vpn'
#   ssh mini 'sudo -n /usr/local/sbin/vpn-status.sh'
set -e
[ "$(id -u)" = 0 ] || { echo "нужен root: sudo sh $0"; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
OWNER=$(stat -f %Su "$SRC")
SUDOERS=/etc/sudoers.d/cc-bodywithoutorgans-vpn

# Скрипт лежит у root и правится только root: иначе NOPASSWD на него = дыра.
install -d -m 755 /usr/local/sbin
install -o root -g wheel -m 755 "$SRC/vpn-status.sh" /usr/local/sbin/vpn-status.sh

TMP=$(mktemp /tmp/cc-vpn-sudoers.XXXXXX)
cat > "$TMP" <<SUDO
# Управление домашним VPN на mini без пароля (deploy/install-vpn-sudoers.sh).
# Только эти команды — ничего с wildcard, аргументы совпадают точно.
$OWNER ALL=(root) NOPASSWD: /bin/launchctl kickstart -k system/cc.bodywithoutorgans.vpn
$OWNER ALL=(root) NOPASSWD: /bin/launchctl kickstart -k system/cc.bodywithoutorgans.directroutes
$OWNER ALL=(root) NOPASSWD: /bin/launchctl bootout system/cc.bodywithoutorgans.vpn
$OWNER ALL=(root) NOPASSWD: /bin/launchctl bootstrap system /Library/LaunchDaemons/cc.bodywithoutorgans.vpn.plist
$OWNER ALL=(root) NOPASSWD: /bin/launchctl print system/cc.bodywithoutorgans.vpn
$OWNER ALL=(root) NOPASSWD: /usr/local/sbin/vpn-status.sh
SUDO

# Битый файл в sudoers.d ломает sudo целиком — проверяем до установки.
visudo -c -f "$TMP" >/dev/null || { echo "sudoers не проходит проверку, не ставлю"; rm -f "$TMP"; exit 1; }
install -o root -g wheel -m 440 "$TMP" "$SUDOERS"
rm -f "$TMP"
visudo -c >/dev/null && echo "ok: $SUDOERS установлен"

echo "--- проверка от $OWNER:"
sudo -u "$OWNER" sudo -n /usr/local/sbin/vpn-status.sh || echo "проверка не прошла"
