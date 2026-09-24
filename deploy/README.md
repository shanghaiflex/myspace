# Деплой на bodywithoutorgans.cc

Схема: Mac mini дома держит копию сайта в `~/movies`, запускает `serve.py` на localhost:8787
с паролем из `.env` (launchd-агент `cc.bodywithoutorgans.serve`), а Cloudflare Tunnel (агент
`cc.bodywithoutorgans.tunnel`) публикует его как https://bodywithoutorgans.cc. Открытых портов нет, HTTPS даёт Cloudflare.

На mini нет Xcode CLT, Homebrew и git, поэтому инструменты стоят автономно в домашней папке
(`deploy/install-tools-offline.sh`: Python в `~/.local/python312`, `~/bin/yt-dlp`, `~/bin/cloudflared`),
а данные ходят через rsync: `scripts/sync.sh` на ноутбуке забирает JSON с правками с сайта, коммитит
в git и заливает весь сайт обратно на mini. Запускать перед и после правок через Claude Code.
Mini выходит в интернет только через AmneziaVPN, без него туннель, погода и загрузки не работают.

## LIVE (2026-09-07): https://bodywithoutorgans.cc

Работает и переживает перезагрузку mini. Три службы на mini (user sergeyfilatov, автологин включён):
- `cc.bodywithoutorgans.serve` (LaunchAgent) — `serve.py` на 127.0.0.1:8787 (пароль в `~/movies/.env`).
- `cc.bodywithoutorgans.vpn` (LaunchDaemon, root, `/usr/local/sbin/awg-mini.sh` = `deploy/awg-mini.sh`) —
  AmneziaWG full-tunnel через **Amnezia Premium, Финляндия** (с 24.09.2026; до этого — свой VPS Server 1).
  Скрипт сервер не знает: он поднимает любой «родной» конфиг AmneziaWG из `/usr/local/etc/amneziawg/mini.conf`
  (root, 0600, в git его нет) через amneziawg-go из AmneziaVPN.app. KeepAlive перезапускает при падении.
- `cc.bodywithoutorgans.tunnel` (LaunchAgent) — cloudflared `--protocol http2` (QUIC режет провайдер).

Почему через VPN: домашний провайдер mini рвёт долгие соединения к Cloudflare и блокирует QUIC.
ВАЖНО: у mini свой конфиг, не ноутбучный — один WG-пир на двух устройствах дерётся (07.09 на Server 1 это
давало 25–40% потерь). Для Premium это отдельное «устройство» в подписке (7 мест; mini — `e4cc1d24…`, macos, fi).

### Сменить VPN / страну / перевыпустить конфиг

Ключ Premium `vpn://…` — не конфиг, а api_key к шлюзу `gw.amnezia.org`; конфиг выдаётся под устройство.
`deploy/amnezia-premium.py` делает это без GUI (как «конфиг для роутера» в приложении), ключ читает из stdin:

```
pbpaste | python3 deploy/amnezia-premium.py info                 # срок, устройства, страны
pbpaste | python3 deploy/amnezia-premium.py config fi > /tmp/m.conf && scp /tmp/m.conf mini:/tmp/awg-premium.conf && rm /tmp/m.conf
ssh -t mini 'sudo sh movies/deploy/install-vpn.sh /tmp/awg-premium.conf'   # пароль sudo — вводит человек
```

`install-vpn.sh` кладёт конфиг, ставит `awg-mini.sh`, `vpn-status.sh` и прямые маршруты, перезапускает
демон, ждёт рукопожатия и печатает внешний IP и доступность lkdr. Прежний скрипт остаётся рядом
(`/usr/local/sbin/awg-mini.sh.bak-*`, в нём зашит Server 1) — откат одной командой `cp`.
Без аргумента — только переставить скрипты с тем конфигом, что уже лежит.

Грабли 24.09.2026, чтобы не наступать второй раз:
- Premium говорит на AWG 2.x: кроме Jc/S1–S4/H1–H4/I1 в конфиге `HeaderProtectionKey` (в UAPI — hex, как все
  ключи), `ContentPaddingAddition`, `RekeyAfterTime` и прочие диапазонами «100-120» — `awg-mini.sh` их передаёт.
- **Сервер Premium не пропускает ICMP**, а ipinfo.io на общем IP отвечает 429: ни пинг, ни ipinfo не проверка.
  Проверка — `curl https://api.ipify.org` (должен быть IP сервера Premium).
- `rx_bytes` в `vpn-status.sh` растёт только от **расшифрованных** пакетов: рукопожатие есть и rx растёт — тоннель
  цел, ищи в маршрутах. Подробный лог amneziawg-go — `AWG_LOG_LEVEL=verbose` в окружении демона;
  `sudo -n /usr/local/sbin/vpn-status.sh --full` — что устройство реально приняло (ключи замаскированы).
- **Налоговая не пускает иностранный IP**: `lkdr.nalog.ru` через Premium молча таймаутится. Сеть ФНС
  213.24.64.0/24 идёт мимо VPN (`direct-routes.txt`), как на ноутбуке — исключением сайтов в AmneziaVPN.

Диагностика: `ssh mini`, логи `~/movies/logs/{serve,tunnel}.log` и `/var/log/awg-mini.log`;
состояние VPN одной командой: `ssh mini 'sudo -n /usr/local/sbin/vpn-status.sh'` (рукопожатие должно быть
свежим, rx/tx расти). Перезапуск VPN: `ssh mini 'sudo -n launchctl kickstart -k system/cc.bodywithoutorgans.vpn'`.

Пароль для этих двух команд не спрашивается: `deploy/install-vpn-sudoers.sh` (запускается от root один раз,
`ssh -t mini 'sudo sh movies/deploy/install-vpn-sudoers.sh'`) кладёт `/etc/sudoers.d/cc-bodywithoutorgans-vpn`
с закрытым списком — kickstart/bootout/bootstrap/print только для демонов `cc.bodywithoutorgans.{vpn,directroutes}`
и read-only `vpn-status.sh` (он лежит у root, потому что сокет amneziawg в `/var/run` доступен только root).

Типичная авария (2026-09-11): сайт отдаёт Cloudflare 1033, в `tunnel.log` — `failed to dial to edge:
timeout: no recent network activity`. Причина почти всегда не в туннеле: `utun11` поднят и держит `0/1`,
но сессия WG протухла, и с mini никуда нет выхода (`curl https://api.ipify.org` таймаутится, а LAN и сети
из `direct-routes.txt` работают). Лечится kickstart'ом демона VPN.

**Теперь это чинится само** (2026-09-15, после третьего такого падения). `scripts/vpn_watchdog.sh`,
launchd-агент `cc.bodywithoutorgans.vpnwatchdog` (`deploy/install-vpn-watchdog.sh`), раз в 2 минуты
спрашивает снаружи `https://bodywithoutorgans.cc/healthz` — один запрос через VPN, край Cloudflare,
тоннель и `serve.py`, то есть ровно то, что видит пользователь. Проверять процессы бессмысленно:
в этой аварии все три живы. Первая неудача не считается (икота края), вторая запускает разбор:
`serve.py` молчит локально → kickstart агента serve; рукопожатия нет или оно старше 3 минут →
kickstart демона VPN, ожидание рукопожатия и сразу kickstart cloudflared (иначе он досиживает
свой бэкофф до 64 с); путь наружу цел, а сайта нет → перезапуск одного cloudflared. Повторно рвать
VPN сторож не станет чаще раза в 10 минут (`WATCHDOG_COOLDOWN`) — при обрыве у провайдера это
мешало бы тоннелю подняться. Пока всё хорошо, в лог не пишется ничего: `logs/vpn-watchdog.log` —
это список аварий и починок, а не тиканье.

```
ssh mini tail -20 movies/logs/vpn-watchdog.log
ssh mini 'cd movies && sh scripts/vpn_watchdog.sh --verbose --dry-run'   # проверить, не трогая ничего
```

## Apple Health (2026-09-07)

- `api.bodywithoutorgans.cc` — третий hostname того же туннеля `movies`, тоже на `127.0.0.1:8787`: туда шлёт батчи
  iOS-приложение Health Bridge (`POST /v1/ingest/health/*`, Bearer-токен из `HEALTH_TOKENS` в `~/movies/.env`,
  тот же токен, что был у старого lifeops). Старый агент `com.lifeops.cloudflared.named` (туннель на мёртвый :8080)
  выгружен, plist переименован в `.disabled`.
- Стек lifeops выкорчеван 2026-09-12: агенты `com.lifeops.{startup,workout-notifier,swim-coach,cloudflared.quick}`
  выгружены, их plist'ы переименованы в `.disabled`; все 11 контейнеров в OrbStack остановлены и лишены
  автозапуска (`docker update --restart=no`), сама OrbStack закрыта (`app.start_at_login: false` — стартовала
  из-за `restart=always` у контейнеров). Контейнеры и тома (`lifeops_pgdata` и др.) НЕ удалены, старая история
  Postgres лежит там; оживить: `PATH=$HOME/.orbstack/bin:$PATH docker start <name>`. Живыми оставлены
  `com.lifeops.amnezia.start` (запускает GUI AmneziaVPN, к туннелю отношения не имеет) и `ai.openclaw.gateway`
  — это отдельный проект, не lifeops.
  Главное, что поднимало стек после загрузки, — строка `@reboot ~/bin/lifeops-startup.sh` в crontab
  (`docker compose up -d` в `~/workspace/lifeops`), а не политика рестарта у контейнеров; 2026-09-13 она
  убрана, бэкап старого crontab — `~/crontab-backup-2026-09-13.txt` на mini.
  Зачем: nginx `lifeops-proxy-1` слушал 8080 и после перезагрузки mini успевал занять порт раньше Zigbee2MQTT —
  тот падал по кругу с `EADDRINUSE`, и лампы не отвечали ни расписанию, ни командам.
- Данные лежат в `~/movies/health.db` (SQLite), rsync его не трогает (`--exclude "health.db*"`).
- Часовая заметка: `sh ~/movies/deploy/install-health-review.sh` ставит агент `cc.bodywithoutorgans.health`
  (`scripts/health_review.sh` раз в час, лог `~/movies/logs/health-review.log`). Нужен `~/.local/bin/claude`
  (`curl -fsSL https://claude.ai/install.sh | bash`) и `CLAUDE_CODE_OAUTH_TOKEN=` в `.env` (получить на ноутбуке:
  `claude setup-token`).

- Свет по закату: `sh ~/movies/deploy/install-lamp-schedule.sh` ставит агент `cc.bodywithoutorgans.lampschedule`
  (`scripts/lamp_schedule.py run` раз в 5 минут, лог `~/movies/logs/lamp-schedule.log`) и уносит прежние
  `local.lamp.*` / `local.plug.*` в `~/Library/LaunchAgents/disabled-by-lamp-schedule` (не удаляет).
- История датчиков: `sh ~/movies/deploy/install-sensors.sh` ставит агент `cc.bodywithoutorgans.sensors`
  (`scripts/sensors.py log` раз в 10 минут, лог `~/movies/logs/sensors.log`) — пишет температуру и влажность
  в `health.db`, то есть попадает и в ночной бэкап.

- Чеки ФНС: `sh ~/movies/deploy/install-lkdr.sh` ставит агент `cc.bodywithoutorgans.lkdr`
  (`scripts/lkdr_sync.sh` ежедневно в 07:00, лог `~/movies/logs/lkdr.log`). Ключ и база чеков не ездят
  через rsync — переносить руками: `scp data/lkdr-auth.json data/lkdr-receipts.json mini:movies/data/`.
  Синк стоит перед пересчётом еды в 07:05: тот читает те же чеки.

## Исторические заметки по установке

- Туннель `movies` (id c7369989-9758-43ca-acd9-33773f23d513) создан с mini, CNAME для bodywithoutorgans.cc и www.
- Провайдер режет QUIC, поэтому агент запускает `cloudflared tunnel --protocol http2 … run` (см. plist).
- Сертификат и креды туннеля в `~/.cloudflared` на mini, конфиг там же в `config.yml`.
- Пароль сайта в `~/movies/.env` на mini. Сменить: поправить файл и `launchctl kickstart -k gui/$(id -u)/cc.bodywithoutorgans.serve`.
- Логи: `~/movies/logs/serve.log`, `~/movies/logs/tunnel.log`.

## Один раз на сервере

```
git clone git@github.com:<user>/movies.git ~/movies && cd ~/movies
cp .env.example .env && $EDITOR .env          # MOVIES_PASSWORD=...
brew install yt-dlp                            # mac; на Linux: pip install yt-dlp или apt
./deploy/install-mac.sh                        # или ./deploy/install-linux.sh
./deploy/setup-tunnel.sh                       # логин в Cloudflare в браузере, создаёт туннель и DNS
```

Проверка: `curl -I https://bodywithoutorgans.cc` должен отдать 302 на /login.

## Полезное

- Логи на маке: `logs/serve.log`, `logs/sync.log`; на Linux: `journalctl -u movies -f`.
- Выйти из сессии: https://bodywithoutorgans.cc/logout
- Сменить пароль: поправить `.env`, перезапустить сервис (`launchctl kickstart -k gui/$(id -u)/cc.bodywithoutorgans.serve` или `sudo systemctl restart movies`).
- Тоннель можно посмотреть в Cloudflare Zero Trust → Networks → Tunnels.
