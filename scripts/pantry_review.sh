#!/bin/sh
# Daily food refresh: Gmail receipts → pantry.json → dish ideas from Claude Code (Opus).
#   scripts/pantry_review.sh [--force-recipes]     --force-recipes: regenerate the ideas now
# Runs on the mini from launchd (deploy/install-pantry.sh, daily) and from POST /api/pantry/review.
# Needs `claude` in PATH and CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env, plus the Gmail
# app password in the login keychain: security add-generic-password -a <addr> -s gmail-imap-mcp -w
# PANTRY_MODEL overrides the model (default: opus).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
# --force / --force-recipes both mean: regenerate the ideas now.
FORCE_RECIPES=0
for arg in "$@"; do
  case "$arg" in --force|--force-recipes) FORCE_RECIPES=1 ;; esac
done
MODEL="${PANTRY_MODEL:-opus}"
WORK="${PANTRY_WORKDIR:-$HOME/.pantry-review}"; mkdir -p "$WORK"
STAMP=$(date '+%F %T')

# Mail first: a note about stale data helps nobody.
if ! python3 scripts/pantry.py sync > "$WORK/sync.txt" 2>&1; then
  echo "$STAMP sync failed:"; cat "$WORK/sync.txt"; exit 1
fi
python3 scripts/pantry.py summary > "$WORK/summary.txt"

# Dish ideas change far slower than stock: regenerate only every few days,
# or when asked. Nobody cooks from a list that is different every morning.
if [ "$FORCE_RECIPES" -eq 1 ] || python3 scripts/pantry.py recipes-stale >/dev/null 2>&1; then
  command -v claude >/dev/null || { echo "$STAMP claude not installed"; exit 1; }
  NEED=$(python3 scripts/pantry.py recipes-need)
  [ "$FORCE_RECIPES" -eq 1 ] && NEED=$(python3 -c "print(max($NEED,1))")
  if [ "$NEED" -eq 0 ]; then echo "$STAMP идей уже достаточно"; else
  { cat pantry/RECIPES.md; printf '\n## Мой профиль\n'; python3 scripts/pantry.py profile;
    printf '\n## Сколько блюд нужно\nРовно %s.\n' "$NEED"; } > "$WORK/recipes-prompt.txt"
  cd "$WORK"
  if ! claude -p --model "$MODEL" --tools "" --no-session-persistence --output-format text < recipes-prompt.txt > recipes.json 2> recipes.err; then
    cd "$ROOT"; echo "$STAMP claude (рецепты) не отработал:"; cat "$WORK/recipes.err"; exit 1
  fi
  cd "$ROOT"

  # Second pass: pick the photo by looking at it. Name-matching heuristics
  # returned a carbonara for creamy mushrooms and a jar of green sauce for
  # pelmeni; only eyes catch that. The model gets Read and nothing else --
  # the candidates are already on disk, so it needs no shell.
  CAND="$WORK/cand"; rm -rf "$CAND"; mkdir -p "$CAND"
  python3 - "$WORK/recipes.json" "$CAND" > "$WORK/photos-list.txt" <<'PYEOF'
import json, re, subprocess, sys
recipes_file, cand_dir = sys.argv[1], sys.argv[2]   # cwd is the repo root
raw = open(recipes_file, encoding="utf-8").read()
t = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
items = json.loads(t[t.find("["):t.rfind("]") + 1])
for r in items[:4]:
    q = (r.get("photo_query") or r.get("title") or "").strip()
    if not q:
        continue
    print(f"\n## {r.get('title')}")
    out = subprocess.run([sys.executable, "scripts/pantry.py", "photo-options", q,
                          "--out", cand_dir, "--limit", "6"],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        print(line.split("  ")[0])
PYEOF
  { cat pantry/PHOTOS.md; printf '\n## Блюда и кандидаты\n'; cat "$WORK/photos-list.txt"; } > "$WORK/photos-prompt.txt"
  cd "$WORK"
  if claude -p --model "$MODEL" --tools Read --add-dir "$CAND" --no-session-persistence --output-format text < photos-prompt.txt > photos.json 2> photos.err; then
    cd "$ROOT"
    python3 scripts/pantry.py recipes-merge-photos --recipes "$WORK/recipes.json" --choice "$WORK/photos.json"
  else
    cd "$ROOT"; echo "$STAMP claude (выбор фото) не отработал, беру автоматический подбор:"; cat "$WORK/photos.err"
  fi
  python3 scripts/pantry.py recipes-save --model "$MODEL" --file "$WORK/recipes.json" \
    --candidates "$CAND" --append || echo "$STAMP рецепты не сохранились"
  fi
fi

# Cheap no-op when everything is in place; a broken image is the one failure
# the page cannot hide, and files go missing through bugs and half-deploys.
python3 scripts/pantry.py photos-repair

# Recount at the end: the earlier summary ran before the recipes were saved,
# so reusing it logs "0 идей блюд" right after saving four of them.
echo "$STAMP $(python3 scripts/pantry.py summary)"
