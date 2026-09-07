#!/bin/sh
# Sync the catalog with the Mac mini that serves bodywithoutorgans.cc. Run from the laptop.
#   1. pull data files edited on the site (movies/mixes/lectures/books json) from the mini
#   2. commit them here
#   3. push the whole site (code, posters, covers, backgrounds) to the mini
# The mini has no git, so this is rsync over SSH. Host alias "mini" comes from ~/.ssh/config.
cd "$(dirname "$0")/.." || exit 1
HOST="${MINI_HOST:-mini}"
DATA="movies.json mixes.json lectures.json books.json"
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" true 2>/dev/null; then
  echo "$HOST is not reachable, nothing synced"; exit 1
fi
# --update: never overwrite a local catalog that is newer than the mini's
# (otherwise a sync right after editing here silently reverts the edit)
for f in $DATA; do rsync -az --update "$HOST:movies/$f" "./$f" 2>/dev/null; done
git add -A
git diff --cached --quiet || git commit -qm "Sync from $HOST $(date '+%Y-%m-%d %H:%M')"
rsync -az --delete --exclude .git --exclude audio --exclude .env --exclude .session_secret --exclude logs --exclude __pycache__ --exclude .DS_Store ./ "$HOST:movies/"
# lecture audio: the mini cannot reach YouTube without a VPN, so audio downloaded on the laptop is pushed too (never deleted remotely)
[ -d audio ] && rsync -az --exclude '*.part' audio/ "$HOST:movies/audio/"
echo "synced with $HOST"
