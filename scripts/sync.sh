#!/bin/sh
# Two-way sync of the catalog through git: commit local edits, pull others', push.
# Safe to run from cron/launchd every few minutes on the server, and by hand on the laptop.
cd "$(dirname "$0")/.." || exit 1
git add -A
if ! git diff --cached --quiet; then
  git commit -qm "Sync from $(hostname -s) $(date '+%Y-%m-%d %H:%M')" || exit 1
fi
git remote get-url origin >/dev/null 2>&1 || { echo "no remote, local commit only"; exit 0; }
if ! git pull -q --rebase; then
  echo "rebase conflict, keeping local version of data files"
  git checkout --theirs movies.json mixes.json 2>/dev/null
  git add -A && GIT_EDITOR=true git rebase --continue || { git rebase --abort; exit 1; }
fi
git push -q
