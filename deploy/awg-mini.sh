#!/bin/bash
# Headless AmneziaWG на mini: full tunnel без GUI AmneziaVPN, под launchd-демоном
# cc.bodywithoutorgans.vpn (KeepAlive — упал процесс, launchd поднимет заново).
#
# Сервер не зашит в скрипт: он читает обычный конфиг AmneziaWG ([Interface]/[Peer], как его
# экспортирует приложение AmneziaVPN) из $CONF. Сменить сервер = положить другой файл
# (deploy/install-vpn.sh) — скрипт при этом не меняется.
#
# Маршруты: 0/1 и 128/1 в тоннель (конкретнее дефолтного, дефолт провайдера не трогаем),
# endpoint — host-маршрутом мимо тоннеля через домашний роутер. direct-routes.sh
# (отдельный демон) потом кладёт поверх свои сети напрямую.
#
# Ставится в /usr/local/sbin от root, конфиг — /usr/local/etc/amneziawg/mini.conf (0600).
set -u
CONF="${AWG_CONF:-/usr/local/etc/amneziawg/mini.conf}"
IFACE="${AWG_IFACE:-utun11}"
AWG_GO="${AWG_GO:-/Applications/AmneziaVPN.app/Contents/MacOS/amneziawg-go}"
RUN=/var/run/amneziawg
SOCK="$RUN/$IFACE.sock"

log() { echo "$(date '+%F %T') $*"; }
die() { log "$*"; sleep 10; exit 1; }   # пауза, чтобы KeepAlive не молотил впустую

[ -r "$CONF" ] || die "нет конфига $CONF"
[ -x "$AWG_GO" ] || die "нет $AWG_GO"

# --- разбор конфига: ключи [Interface] и [Peer] в переменные I_* / P_*
section=""
while IFS= read -r raw; do
  line=$(echo "$raw" | sed 's/[#;].*$//; s/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -n "$line" ] || continue
  case "$line" in
    "[Interface]") section=I; continue ;;
    "[Peer]") section=P; continue ;;
    \[*) section=""; continue ;;
  esac
  key=$(echo "${line%%=*}" | tr -d ' \t' | tr '[:upper:]' '[:lower:]')
  val=$(echo "${line#*=}" | sed 's/^[[:space:]]*//')
  # имена ключей — только буквы и цифры, чтобы в имя переменной не попало ничего чужого
  echo "$key" | grep -Eq '^[a-z0-9]+$' || continue
  [ -n "$section" ] && printf -v "${section}_${key}" '%s' "$val"
done < "$CONF"

hex() { echo "$1" | base64 -D 2>/dev/null | od -An -tx1 | tr -d ' \n'; }

addr=$(echo "${I_address:-}" | tr ',' '\n' | tr -d ' ' | grep -m1 -E '^[0-9.]+(/[0-9]+)?$' | cut -d/ -f1)
[ -n "$addr" ] || die "в конфиге нет IPv4 Address"
[ -n "${I_privatekey:-}" ] && [ -n "${P_publickey:-}" ] && [ -n "${P_endpoint:-}" ] || die "конфиг неполный"

ep_host="${P_endpoint%:*}"; ep_port="${P_endpoint##*:}"
ep_host="${ep_host#[}"; ep_host="${ep_host%]}"
if echo "$ep_host" | grep -Eq '^[0-9.]+$'; then
  ep_ip="$ep_host"
else
  # имя резолвится до подъёма тоннеля — через DNS домашнего роутера
  for _ in 1 2 3 4 5 6; do
    ep_ip=$(dscacheutil -q host -a name "$ep_host" 2>/dev/null | awk '/^ip_address:/{print $2; exit}')
    [ -n "$ep_ip" ] && break; sleep 5
  done
  [ -n "${ep_ip:-}" ] || die "не резолвится $ep_host"
fi

# домашний шлюз — роутер по DHCP, как в direct-routes.sh
GW=""
for i in $(ifconfig -l); do
  case "$i" in en*) ;; *) continue ;; esac
  r=$(ipconfig getoption "$i" router 2>/dev/null)
  case "$r" in ""|0.0.0.0) continue ;; esac
  GW="$r"; break
done
[ -n "$GW" ] || die "нет домашнего шлюза (сеть не поднялась?)"

# --- старый процесс на этом интерфейсе мог пережить нас
pkill -f "amneziawg-go $IFACE" 2>/dev/null; sleep 1
rm -f "$SOCK"
mkdir -p "$RUN"

# AWG_LOG_LEVEL=verbose (в plist) пишет в /var/log/awg-mini.log каждое рукопожатие и keepalive — для разбора
# аварий; по умолчанию только ошибки, иначе лог растёт на мегабайт в день
LOG_LEVEL="${AWG_LOG_LEVEL:-error}" WG_PROCESS_FOREGROUND=1 "$AWG_GO" "$IFACE" &
PID=$!
for _ in $(seq 1 50); do [ -S "$SOCK" ] && break; sleep 0.2; done
[ -S "$SOCK" ] || { kill "$PID" 2>/dev/null; die "amneziawg-go не открыл $SOCK"; }

cleanup() {
  route -n delete -net 0.0.0.0/1 >/dev/null 2>&1
  route -n delete -net 128.0.0.0/1 >/dev/null 2>&1
  route -n delete -host "$ep_ip" >/dev/null 2>&1
  kill "$PID" 2>/dev/null
  rm -f "$RUN/mini.iface"
}
trap 'cleanup; exit 0' TERM INT

# --- UAPI: ключи в hex, параметры обфускации AmneziaWG как есть
{
  echo "set=1"
  echo "private_key=$(hex "$I_privatekey")"
  [ -n "${I_listenport:-}" ] && echo "listen_port=$I_listenport"
  for k in jc jmin jmax s1 s2 s3 s4 h1 h2 h3 h4 i1 i2 i3 i4 i5 j1 j2 j3 itime; do
    v=$(eval "echo \"\${I_$k:-}\"")
    [ -n "$v" ] && echo "$k=$v"
  done
  # параметры AWG 2.x (Amnezia Premium, 09.2026): ключ защиты заголовков — в hex, как все ключи,
  # диапазоны вида «100-120» amneziawg-go разбирает сам
  [ -n "${I_headerprotectionkey:-}" ] && echo "header_protection_key=$(hex "$I_headerprotectionkey")"
  for pair in contentpaddingaddition:content_padding_addition rekeyaftertime:rekey_after_time \
              rekeytimeout:rekey_timeout rejectaftertime:reject_after_time \
              keepalivetimeout:keepalive_timeout maxhandshakeattempts:max_handshake_attempts; do
    v=$(eval "echo \"\${I_${pair%%:*}:-}\"")
    [ -n "$v" ] && echo "${pair#*:}=$v"
  done
  echo "replace_peers=true"
  echo "public_key=$(hex "$P_publickey")"
  [ -n "${P_presharedkey:-}" ] && echo "preshared_key=$(hex "$P_presharedkey")"
  echo "endpoint=$ep_ip:$ep_port"
  echo "persistent_keepalive_interval=${P_persistentkeepalive:-25}"
  echo "replace_allowed_ips=true"
  echo "allowed_ip=0.0.0.0/0"
  echo
} | nc -U "$SOCK" | grep -q '^errno=0' || { cleanup; die "UAPI отверг конфиг"; }

ifconfig "$IFACE" inet "$addr" "$addr" netmask 255.255.255.255 mtu "${I_mtu:-1376}" up
route -n delete -host "$ep_ip" >/dev/null 2>&1
route -n add -host "$ep_ip" "$GW" >/dev/null
for net in 0.0.0.0/1 128.0.0.0/1; do
  route -n delete -net "$net" >/dev/null 2>&1
  route -n add -net "$net" -interface "$IFACE" >/dev/null
done
echo "$IFACE" > "$RUN/mini.iface"
log "tunnel up on $IFACE (pid $PID) → $ep_host ($ep_ip:$ep_port), addr $addr"

# direct-routes: тоннель только что перестроил таблицу — вернуть прямые сети сразу, не ждать 5 минут
[ -x /usr/local/sbin/direct-routes.sh ] && /usr/local/sbin/direct-routes.sh >/dev/null 2>&1 &

wait "$PID"
log "amneziawg-go завершился (код $?)"
cleanup
exit 1
