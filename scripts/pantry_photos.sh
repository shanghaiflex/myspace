#!/bin/sh
# Re-pick dish photos by actually looking at them.
#   scripts/pantry_photos.sh            # only dishes currently without a photo
#   scripts/pantry_photos.sh --all      # every dish on the page
# Two steps: download candidates, then let Codex inspect the attached images.
# The model has no file or shell tools. Name matching cannot tell a carbonara
# from creamy mushrooms; eyes can.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PATH="$HOME/.local/bin:$HOME/.local/python312/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
MODEL=$(python3 "$ROOT/scripts/codex_ask.py" --resolve-model "${PANTRY_MODEL:-}")
WORK="${PANTRY_WORKDIR:-$HOME/.pantry-review}"; mkdir -p "$WORK"
CAND="$WORK/cand"; rm -rf "$CAND"; mkdir -p "$CAND"
STAMP=$(date '+%F %T')

MISSING="--missing"
[ "$1" = "--all" ] && MISSING=""

python3 scripts/pantry.py photos-plan --out "$CAND" $MISSING > "$WORK/photos-list.txt"
if [ ! -s "$WORK/photos-list.txt" ]; then echo "$STAMP нечего перевыбирать"; exit 0; fi
command -v codex >/dev/null || { echo "codex not installed; see docs/ai.md"; exit 1; }

{ cat pantry/PHOTOS.md; printf '\n## Блюда и кандидаты\n'; cat "$WORK/photos-list.txt"; } > "$WORK/photos-prompt.txt"
cd "$WORK"
if ! python3 "$ROOT/scripts/codex_ask.py" --model "$MODEL" --json --images-dir "$CAND" < photos-prompt.txt > photos.json 2> photos.err; then
  cd "$ROOT"; echo "$STAMP codex (выбор фото) не отработал:"; cat "$WORK/photos.err"; exit 1
fi
cd "$ROOT"
python3 scripts/pantry.py photos-apply --choice "$WORK/photos.json" --candidates "$CAND"
python3 scripts/pantry.py photos-repair
