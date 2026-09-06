#!/bin/sh
# Serve the catalog locally with the edit API: http://localhost:8787
cd "$(dirname "$0")"
exec python3 serve.py "${1:-8787}"
