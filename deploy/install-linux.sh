#!/bin/sh
# Runs serve.py and the git sync as systemd units on a Linux VPS. Needs python3, git, yt-dlp.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; U="$(id -un)"
[ -f "$ROOT/.env" ] || { echo "create $ROOT/.env with MOVIES_PASSWORD=... first (see .env.example)"; exit 1; }
sudo tee /etc/systemd/system/movies.service >/dev/null <<UNIT
[Unit]
Description=Movies & mixes site
After=network.target
[Service]
User=$U
WorkingDirectory=$ROOT
ExecStart=$(command -v python3) $ROOT/serve.py 8787
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/systemd/system/movies-sync.service >/dev/null <<UNIT
[Unit]
Description=Movies git sync
[Service]
Type=oneshot
User=$U
ExecStart=/bin/sh $ROOT/scripts/sync.sh
UNIT
sudo tee /etc/systemd/system/movies-sync.timer >/dev/null <<UNIT
[Unit]
Description=Movies git sync every 5 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now movies.service movies-sync.timer
sleep 1; curl -s http://localhost:8787/api/ping && echo " ← serve.py is up"
