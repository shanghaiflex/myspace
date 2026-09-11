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
- `cc.bodywithoutorgans.vpn` (LaunchDaemon, root, `/usr/local/sbin/awg-mini.sh`) — AmneziaWG full-tunnel
  на выделенном конфиге Server 1 (адрес 10.8.1.5, отдельные ключи — НЕ те же, что у ноутбука 10.8.1.1).
  Через свой amneziawg-go, KeepAlive перезапускает при падении. Ключи только на mini, в git их нет.
- `cc.bodywithoutorgans.tunnel` (LaunchAgent) — cloudflared `--protocol http2` (QUIC режет провайдер).

Почему через VPN: домашний провайдер mini рвёт долгие соединения к Cloudflare и блокирует QUIC.
Cloudflared идёт через VPN Server 1 (у него чистый путь до Cloudflare, 0% потерь). ВАЖНО: у mini
свой конфиг Server 1 (10.8.1.5); общий с ноутбуком конфиг (10.8.1.1) давал конфликт одного WG-пира и 25–40% потерь.

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

## Apple Health (2026-09-07)

- `api.bodywithoutorgans.cc` — третий hostname того же туннеля `movies`, тоже на `127.0.0.1:8787`: туда шлёт батчи
  iOS-приложение Health Bridge (`POST /v1/ingest/health/*`, Bearer-токен из `HEALTH_TOKENS` в `~/movies/.env`,
  тот же токен, что был у старого lifeops). Старый агент `com.lifeops.cloudflared.named` (туннель на мёртвый :8080)
  выгружен, plist переименован в `.disabled`. Остальные `com.lifeops.*` агенты (startup, workout-notifier, swim-coach)
  и `ai.openclaw.gateway` не трогал — colima там сломана, они просто падают.
- Данные лежат в `~/movies/health.db` (SQLite), rsync его не трогает (`--exclude "health.db*"`).
- Часовая заметка: `sh ~/movies/deploy/install-health-review.sh` ставит агент `cc.bodywithoutorgans.health`
  (`scripts/health_review.sh` раз в час, лог `~/movies/logs/health-review.log`). Нужен `~/.local/bin/claude`
  (`curl -fsSL https://claude.ai/install.sh | bash`) и `CLAUDE_CODE_OAUTH_TOKEN=` в `.env` (получить на ноутбуке:
  `claude setup-token`).

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
