#!/bin/sh
# Ежедневная выкачка новых чеков из lkdr.nalog.ru (launchd cc.bodywithoutorgans.lkdr).
#   sh scripts/lkdr_sync.sh
set -e
cd "$(dirname "$0")/.."
PY="$HOME/.local/python312/bin/python3"; [ -x "$PY" ] || PY=python3
echo "$(date '+%Y-%m-%d %H:%M:%S') $("$PY" scripts/lkdr.py sync 2>&1 | tail -1)"
