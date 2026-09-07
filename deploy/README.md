# Деплой на bodywithoutorgans.cc

Схема: Mac mini дома держит копию сайта в `~/movies`, запускает `serve.py` на localhost:8787
с паролем из `.env` (launchd-агент `cc.bodywithoutorgans.serve`), а Cloudflare Tunnel (агент
`cc.bodywithoutorgans.tunnel`) публикует его как https://bodywithoutorgans.cc. Открытых портов нет, HTTPS даёт Cloudflare.

На mini нет Xcode CLT, Homebrew и git, поэтому инструменты стоят автономно в домашней папке
(`deploy/install-tools-offline.sh`: Python в `~/.local/python312`, `~/bin/yt-dlp`, `~/bin/cloudflared`),
а данные ходят через rsync: `scripts/sync.sh` на ноутбуке забирает JSON с правками с сайта, коммитит
в git и заливает весь сайт обратно на mini. Запускать перед и после правок через Claude Code.
Mini выходит в интернет только через AmneziaVPN, без него туннель, погода и загрузки не работают.

## Что уже сделано (2026-09-07)

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
