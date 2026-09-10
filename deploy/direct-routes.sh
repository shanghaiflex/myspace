#!/bin/sh
# Split tunneling для mini: перечисленные в direct-routes.txt сети идут напрямую
# через домашнего провайдера, всё остальное — в VPN (awg-mini.sh ставит 0/1 и 128.0/1
# на utun, наши /18…/24 конкретнее и потому выигрывают).
#
# Ставится в /usr/local/sbin/direct-routes.sh демоном cc.bodywithoutorgans.directroutes
# (deploy/install-direct-routes.sh), запускается при загрузке и раз в 5 минут: тоннель
# при перезапуске сбрасывает таблицу маршрутов, маршруты надо возвращать.
set -u
LIST="${DIRECT_ROUTES_LIST:-/usr/local/etc/direct-routes.txt}"
[ -f "$LIST" ] || { echo "no list at $LIST"; exit 1; }

# Физический интерфейс = тот, у которого DHCP отдал роутер (utun'ы его не отдают).
IF=""; GW=""
for i in $(ifconfig -l); do
  case "$i" in en*) ;; *) continue ;; esac
  r=$(ipconfig getoption "$i" router 2>/dev/null)
  case "$r" in ""|0.0.0.0) continue ;; esac
  ifconfig "$i" 2>/dev/null | grep -q "inet " || continue
  IF="$i"; GW="$r"; break
done
[ -n "$GW" ] || { echo "$(date '+%F %T') no LAN gateway found, nothing to do"; exit 0; }

added=0; kept=0
while read -r line; do
  net=$(echo "$line" | sed 's/#.*//' | tr -d ' \t')
  [ -n "$net" ] || continue
  # только CIDR — список лежит в репозитории, в root-скрипт не должно попасть ничего другого
  echo "$net" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$' || { echo "skip bad line: $net"; continue; }
  cur=$(route -n get -net "$net" 2>/dev/null | awk '/gateway:/{print $2}')
  if [ "$cur" = "$GW" ]; then kept=$((kept+1)); continue; fi
  route -n add -net "$net" "$GW" >/dev/null 2>&1 || route -n change -net "$net" "$GW" >/dev/null 2>&1
  added=$((added+1))
done < "$LIST"
echo "$(date '+%F %T') direct routes via $GW ($IF): +$added, already $kept"
