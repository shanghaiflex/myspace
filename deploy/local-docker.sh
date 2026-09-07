#!/bin/sh
# Always-on local instance on the laptop (colima/Docker): http://localhost:8787
# Rebuilds the image and (re)starts the container; the repo is bind-mounted, so no rebuild is needed for data or html changes.
set -e
cd "$(dirname "$0")/.."
docker build -q -t movies:local . >/dev/null
docker rm -f movies >/dev/null 2>&1 || true
docker run -d --name movies --restart unless-stopped -p 127.0.0.1:8787:8787 -v "$PWD:/app" -e MOVIES_BIND=0.0.0.0 movies:local >/dev/null
sleep 2; curl -s http://localhost:8787/api/ping && echo " ← movies container is up"
