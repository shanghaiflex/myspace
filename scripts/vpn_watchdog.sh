#!/bin/sh
# Сторож пути наружу: проверяет не процессы, а результат — открывается ли сайт снаружи.
#   scripts/vpn_watchdog.sh [--dry-run] [--verbose]
# На mini его раз в 2 минуты запускает launchd (deploy/install-vpn-watchdog.sh).
#
# Зачем он нужен. KeepAlive у демона VPN ловит только падение процесса, а настоящая авария
# (2026-09-11, 2026-09-15) выглядит иначе: amneziawg-go жив, utun поднят и держит маршрут 0/1,
# но WG-сессия протухла — рукопожатия нет, rx стоит на нуле, и наружу с mini не выходит ничего.
# Cloudflared в этот момент крутит «dial tcp …:7844: i/o timeout», сайт отдаёт Cloudflare 1033 (530),
# и так до тех пор, пока это не заметит человек. Заметить должен сторож.
#
# Проверка одна и сквозная: GET /healthz по внешнему адресу. Она меряет всю цепочку сразу,
# поэтому не врёт в обе стороны: не бьёт тревогу, когда всё работает «не тем путём», и не молчит,
# когда каждый кусок по отдельности жив, а вместе не складывается.
#
# Лечение — лестницей, от дешёвого к дорогому, и только то, что подтвердилось проверкой:
#   serve.py не отвечает локально → kickstart агента serve (VPN тут ни при чём);
#   рукопожатия нет или оно старое   → kickstart демона VPN, ждём рукопожатия, потом cloudflared
#                                      (он сидит в бэкоффе до 64 с и сам бы столько молчал);
#   путь наружу цел, а сайта нет     → kickstart одного cloudflared.
# Пока всё хорошо, скрипт молчит: в лог попадают только авария, починка и возвращение сайта.
set -u
cd "$(dirname "$0")/.."
ROOT=$(pwd)
[ -f .env ] && { set -a; . ./.env; set +a; }

URL="${WATCHDOG_URL:-https://bodywithoutorgans.cc/healthz}"
LOCAL_URL="${WATCHDOG_LOCAL_URL:-http://127.0.0.1:8787/healthz}"
TIMEOUT="${WATCHDOG_TIMEOUT:-12}"
RETRY_WAIT="${WATCHDOG_RETRY_WAIT:-10}"     # пауза перед вторым запросом: одиночная икота края — не авария
COOLDOWN="${WATCHDOG_COOLDOWN:-600}"        # не дёргать VPN чаще раза в 10 минут (иначе при обрыве у провайдера
                                            # сторож будет рвать тоннель по кругу и мешать ему подняться)
STALE="${WATCHDOG_STALE_HANDSHAKE:-180}"    # рукопожатие старше — считаем протухшим
STATE="$ROOT/logs/vpn-watchdog.state"
DRY=0; VERBOSE=0
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --verbose) VERBOSE=1;; esac; done

mkdir -p "$ROOT/logs"
log() { echo "$(date '+%F %T') $*"; }
now=$(date +%s)

# Состояние между запусками: было ли уже плохо (чтобы написать про возвращение) и когда чинили в прошлый раз.
prev_state=ok; down_since=0; last_vpn=0; last_tunnel=0
[ -f "$STATE" ] && . "$STATE" 2>/dev/null || true
save_state() {
  printf 'prev_state=%s\ndown_since=%s\nlast_vpn=%s\nlast_tunnel=%s\n' \
    "$1" "$2" "$last_vpn" "$last_tunnel" > "$STATE"
}

probe() { curl -sS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000; }

# Сайт вернулся (или не уходил): сообщаем только если до этого было плохо.
report_ok() {
  if [ "$prev_state" = down ] && [ "$down_since" -gt 0 ]; then
    log "сайт снова отвечает, лежал $(( (now - down_since) / 60 )) мин $(( (now - down_since) % 60 )) с"
  elif [ "$VERBOSE" = 1 ]; then
    log "всё в порядке ($URL → 200)"
  fi
  save_state ok 0
  exit 0
}

code=$(probe "$URL")
[ "$code" = 200 ] && report_ok
sleep "$RETRY_WAIT"
code2=$(probe "$URL")
[ "$code2" = 200 ] && report_ok

[ "$prev_state" = down ] || down_since=$now
log "сайт не отвечает: $URL → $code, повтор → $code2"

if [ "$DRY" = 1 ]; then log "--dry-run: дальше был бы разбор и kickstart"; save_state down "$down_since"; exit 1; fi

kick_agent() {  # LaunchAgent пользователя: cloudflared и serve, пароль не нужен
  launchctl kickstart -k "gui/$(id -u)/$1" 2>/dev/null && log "перезапущен $1" || log "не удалось перезапустить $1"
}

# 1. serve.py. Если не отвечает даже локально — до VPN дело не дошло, чинить надо его.
lcode=$(probe "$LOCAL_URL")
if [ "$lcode" != 200 ]; then
  log "serve.py не отвечает локально ($LOCAL_URL → $lcode)"
  kick_agent cc.bodywithoutorgans.serve
  sleep 5
  [ "$(probe "$LOCAL_URL")" = 200 ] && log "serve.py поднялся" || log "serve.py всё ещё молчит — нужен человек"
fi

# 2. Путь наружу. Рукопожатие видно только root, поэтому через read-only vpn-status.sh (sudoers, без пароля).
status=$(sudo -n /usr/local/sbin/vpn-status.sh 2>/dev/null)
hs_line=$(echo "$status" | grep -m1 'рукопожатие' || echo 'рукопожатие: неизвестно')
hs_age=$(echo "$hs_line" | awk '{ if ($2 ~ /^[0-9]+$/) print $2; else print -1 }')
log "VPN: $hs_line, $(echo "$status" | grep -m1 '^rx:' || echo 'rx: ?')"

if [ "$hs_age" -lt 0 ] || [ "$hs_age" -gt "$STALE" ]; then
  if [ $((now - last_vpn)) -lt "$COOLDOWN" ]; then
    log "VPN перезапускался $(( (now - last_vpn) / 60 )) мин назад — жду, не рву повторно"
  else
    sudo -n launchctl kickstart -k system/cc.bodywithoutorgans.vpn 2>/dev/null \
      && { log "перезапущен VPN"; last_vpn=$now; } || log "не удалось перезапустить VPN"
    i=0; while [ $i -lt 20 ]; do
      sleep 2; i=$((i + 2))
      age=$(sudo -n /usr/local/sbin/vpn-status.sh 2>/dev/null | awk '/рукопожатие/ { if ($2 ~ /^[0-9]+$/) print $2; else print -1 }')
      [ "${age:--1}" -ge 0 ] && { log "рукопожатие вернулось через $i с"; break; }
    done
    # Cloudflared после починки VPN сидит в бэкоффе до 64 с — не ждём его, а будим сразу.
    kick_agent cc.bodywithoutorgans.tunnel; last_tunnel=$now
  fi
elif [ $((now - last_tunnel)) -ge "$COOLDOWN" ]; then
  # VPN цел, а сайта нет — значит завис сам cloudflared.
  log "VPN в порядке, перезапускаю только cloudflared"
  kick_agent cc.bodywithoutorgans.tunnel; last_tunnel=$now
else
  log "cloudflared перезапускался $(( (now - last_tunnel) / 60 )) мин назад — жду"
fi

# 3. Проверяем, что починили: тоннель регистрирует соединения несколько секунд.
i=0; while [ $i -lt 45 ]; do
  sleep 5; i=$((i + 5))
  if [ "$(probe "$URL")" = 200 ]; then
    log "сайт поднялся через $i с после починки (лежал $(( (now - down_since) / 60 )) мин)"
    save_state ok 0
    exit 0
  fi
done
log "сайт не поднялся — следующая попытка через 2 минуты"
save_state down "$down_since"
exit 1
