#!/bin/sh
# Serve the catalog locally: http://localhost:8787
cd "$(dirname "$0")"
PORT="${1:-8787}"
echo "Movies → http://localhost:$PORT"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
