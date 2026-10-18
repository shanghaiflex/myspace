# Выкладка, mini, VPN, сторож, ноутбук

_Вынесено из `CLAUDE.md` 18.10.2026 слово в слово; индекс модулей и общие правила — в `CLAUDE.md`._

## Auth & deploy
`serve.py` requires a password when `MOVIES_PASSWORD` is set (env or `.env`, gitignored); localhost runs open.
Production: https://bodywithoutorgans.cc served by the home Mac mini (ssh alias `mini`, user sergeyfilatov,
site in `~/movies`, launchd agents `cc.bodywithoutorgans.serve` / `.tunnel`), LIVE since 2026-09-07 at https://bodywithoutorgans.cc. Three services on the mini: serve (agent), a root VPN
LaunchDaemon `cc.bodywithoutorgans.vpn` (`deploy/awg-mini.sh` → `/usr/local/sbin`, runs a headless AmneziaWG full
tunnel from `/usr/local/etc/amneziawg/mini.conf` — since 01.10.2026 the user's own VPS **Server 2** (Netherlands,
45.131.213.116, mini is its own client 10.8.1.7; the laptop's AmneziaVPN uses the same server as 10.8.1.1). Before
that: Amnezia Premium Finland 24.09–01.10 (its server died at 01:30 on 01.10 and the site was down 13 h) and the
self-hosted Server 1, which died 23.09 and took the site down for 28 h. Switch server = a fresh client config from
AmneziaVPN («Поделиться» → подключение, a `vpn://` string that is a zlib+base64 JSON with the .conf inside, NOT a
Premium key) + `deploy/install-vpn.sh` (needs the sudo password, typed by a human); a copy of the mini's conf is in
the laptop keychain (`amnezia-server2-mini`). Premium path: `deploy/amnezia-premium.py`, see `deploy/README.md`. Premium drops ICMP and nalog.ru
refuses foreign IPs — ФНС 213.24.64.0/24 goes direct via `direct-routes.txt`), and
cloudflared (agent, http2). See `deploy/README.md`.
The mini has no git/brew/CLT; Python lives in `~/.local/python312`, tools in `~/bin`, Claude Code in `~/.local/bin/claude`
(native install, no node needed; `python3` is NOT on the default ssh PATH — use `~/.local/python312/bin/python3`). Data sync is rsync:
after ANY change run `scripts/deploy.sh "message"` — it folds in live in-site edits for JSON you didn't touch,
commits, pushes to GitHub, rsyncs to the mini, and restarts serve. That is the one command to ship to production.
**Сайт поднимается сам.** Демон VPN переживает падение процесса, но настоящая авария (11.09, 15.09.2026)
выглядит иначе: `amneziawg-go` жив, `utun11` держит `0/1`, а WG-сессия протухла — рукопожатия нет, `rx` стоит,
наружу с mini не выходит ничего, cloudflared крутит `dial tcp …:7844: i/o timeout`, сайт отдаёт 530 (Cloudflare 1033).
`scripts/vpn_watchdog.sh` (launchd `cc.bodywithoutorgans.vpnwatchdog`, раз в 2 минуты, `deploy/install-vpn-watchdog.sh`)
поэтому проверяет не процессы, а результат: `GET /healthz` снаружи, одним запросом через VPN → край Cloudflare →
тоннель → `serve.py`. Две неудачи подряд — и лечение лестницей: serve → VPN (+ сразу cloudflared, он бы досиживал
бэкофф до 64 с) → cloudflared. Подробности и ручные команды — `deploy/README.md`.
**Cloudflared сторож не трогает, пока тот держит соединения** (27.09.2026): если serve отвечает, а в метриках
cloudflared (`127.0.0.1:20241/metrics`, `ha_connections`) больше нуля, провал проверки — это путь с mini к краю,
а не сайт. С Amnezia Premium выход VPN через раз теряет SYN до адресов Cloudflare (github при этом мгновенно),
и сторож 5–8 раз в час перезапускал здоровый тоннель, роняя сайт на 2–3 минуты, — «сайт не открывается».
**Но «не трогать» — только когда край не ответил вовсе (000)** (01.10.2026): после смены VPN-сервера cloudflared
держал четыре полуоткрытых соединения через умершую сессию, `ha_connections` показывала 4, край отдавал 502, и
сторож бездействовал, пока тоннель не перезапустили руками. Ответ края кодом (502, 530) значит, что проверка до
него дошла и лежит именно тоннель — его перезапускают.
```
ssh mini tail -20 movies/logs/vpn-watchdog.log      # только аварии и починки, молчит когда всё хорошо
ssh mini 'sudo -n /usr/local/sbin/vpn-status.sh'    # рукопожатие и rx/tx руками
```

The mini reaches SoundCloud/Cloudflare/Open-Meteo directly, and since the VPN LaunchDaemon became a full tunnel
it reaches YouTube too (проверено 15.09.2026: `yt-dlp ytsearch` и `curl youtube.com` → 200) — на этом стоят
и `start_audio_job` (mini сам качает звук лекций из `preload`), и поиск советов по лекциям. Скачивать звук
на ноутбуке (`scripts/lectures.py audio <id>`) и толкать `audio/` на mini через `scripts/sync.sh` по-прежнему
можно, но это уже запасной путь, а не единственный.

## Laptop
The always-on local instance on http://localhost:8787 is OFF since 2026-09-07: production went live, so
the laptop no longer needs its own copy running. Bring it back with `sh deploy/local-docker.sh` (colima +
`docker run`, repo bind-mounted, image `movies:local` is still built); `docker rm -f movies` turns it off
again. `docker-compose.yml` describes the same container but the compose plugin is not installed here —
use the script. The Firefox homepage is production, https://bodywithoutorgans.cc (it was localhost until
the tunnel went live), set in the profile's `user.js`
(`~/Library/Application Support/Firefox/Profiles/oswx4c6l.default-release/user.js`).
The user wants it on startup and the Home button ONLY — new tabs must stay Firefox's own new-tab page.
Do not install a new-tab-override extension for this: «New Tab Homepage» was tried, made every new tab
open the catalog, and is now disabled (`extensions/{66E978CD-…}.xpi.disabled`).
