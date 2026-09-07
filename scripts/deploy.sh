#!/bin/sh
# One command to ship a change to production (the mini at https://bodywithoutorgans.cc).
#   scripts/deploy.sh ["commit message"]
# Steps: fold in any live in-site edits for JSON files you did NOT touch, commit, push to GitHub,
# rsync the whole site to the mini, and restart serve so code changes take effect.
# The mini is the live server; its JSON can also be edited through the site, so we never blindly
# overwrite a data file you didn't change this session.
set -e
cd "$(dirname "$0")/.."
HOST="${MINI_HOST:-mini}"
MSG="${1:-Update site}"
DATA="movies.json mixes.json lectures.json books.json mix_recs.json"

reach() { ssh -o BatchMode=yes -o ConnectTimeout=6 "$HOST" true 2>/dev/null; }
if ! reach; then echo "mini ($HOST) unreachable — committing + pushing only, NOT deploying"; NOMINI=1; fi

# 1. pull live site-edits for JSON files that are clean locally (not edited this session)
if [ -z "$NOMINI" ]; then
  for f in $DATA; do
    if git diff --quiet -- "$f" && git diff --cached --quiet -- "$f"; then
      rsync -az "$HOST:movies/$f" "./$f" 2>/dev/null || true
    else
      echo "keeping local $f (changed this session)"
    fi
  done
fi

# 2. commit everything
git add -A
if git diff --cached --quiet; then echo "nothing to commit"; else git commit -q -m "$MSG"; echo "committed: $MSG"; fi

# 3. push to GitHub
git push -q origin main && echo "pushed to github"

# 4. deploy to the mini
if [ -z "$NOMINI" ]; then
  rsync -az --delete --exclude .git --exclude audio --exclude .env --exclude .session_secret --exclude logs --exclude "health.db*" --exclude pantry.json --exclude dishes --exclude commons_cache --exclude "scripts/pantrylib/data/receipts.json" --exclude __pycache__ --exclude .DS_Store ./ "$HOST:movies/"
  [ -d audio ] && rsync -az --exclude '*.part' audio/ "$HOST:movies/audio/" 2>/dev/null || true
  ssh "$HOST" 'launchctl kickstart -k gui/$(id -u)/cc.bodywithoutorgans.serve >/dev/null 2>&1; sleep 1; curl -s -o /dev/null -w "mini origin: %{http_code}\n" http://127.0.0.1:8787/login'
  echo "deployed to $HOST"
fi
