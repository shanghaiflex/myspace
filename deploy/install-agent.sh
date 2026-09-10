#!/bin/sh
# Браузер для агента на mini: Node + @playwright/mcp + Chromium.
# Запускать на mini от обычного пользователя (root не нужен):
#   ssh mini 'sh movies/deploy/install-agent.sh'
# Split tunneling (чтобы Avito/Ozon вообще открылись) ставится отдельно и от root:
#   ssh -t mini 'sudo sh movies/deploy/install-direct-routes.sh'
set -e
PREFIX="$HOME/.local"
export PATH="$PREFIX/node/bin:$PREFIX/bin:$HOME/bin:$PATH"

if ! command -v node >/dev/null; then
  V=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c 'import json,sys;print(next(x["version"] for x in json.load(sys.stdin) if x["lts"]))')
  echo "ставлю node $V в $PREFIX/node"
  cd /tmp
  curl -fsSL -o node.tar.xz "https://nodejs.org/dist/$V/node-$V-darwin-arm64.tar.xz"
  rm -rf "$PREFIX/node"; mkdir -p "$PREFIX"
  tar -xf node.tar.xz -C "$PREFIX"
  mv "$PREFIX/node-$V-darwin-arm64" "$PREFIX/node"
  rm -f node.tar.xz
  mkdir -p "$HOME/bin"
  for b in node npm npx; do ln -sf "$PREFIX/node/bin/$b" "$HOME/bin/$b"; done
fi
echo "node $(node -v), npm $(npm -v)"

npm i -g @playwright/mcp@latest
npx --yes playwright install chromium chromium-headless-shell
command -v playwright-mcp >/dev/null || { echo "playwright-mcp не встал — проверь $(npm bin -g)"; exit 1; }
echo "playwright-mcp $(playwright-mcp --version)"
