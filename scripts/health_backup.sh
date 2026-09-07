#!/bin/sh
# Nightly snapshot of health.db (launchd agent cc.bodywithoutorgans.healthbackup, see deploy/install-health-backup.sh).
#   sh scripts/health_backup.sh [--keep N]
set -e
cd "$(dirname "$0")/.."
PY="$HOME/.local/python312/bin/python3"; [ -x "$PY" ] || PY=python3
KEEP=7
[ "$1" = "--keep" ] && KEEP="$2"
echo "$(date '+%Y-%m-%d %H:%M:%S') $("$PY" scripts/health.py backup --keep "$KEEP")"
