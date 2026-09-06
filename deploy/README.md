# Деплой на bodywithoutorgans.cc

Схема: сервер (Mac mini дома или Linux VPS) держит клон этого репозитория, запускает `serve.py`
на localhost:8787 с паролем из `.env`, а Cloudflare Tunnel публикует его как https://bodywithoutorgans.cc.
Открытых портов нет, HTTPS даёт Cloudflare. Данные ходят через git: `scripts/sync.sh` раз в 5 минут
коммитит правки с сайта и пушит, ноутбук делает `git pull` перед своими правками.

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
