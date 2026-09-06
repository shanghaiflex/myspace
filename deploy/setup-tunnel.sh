#!/bin/sh
# Exposes the local server at https://bodywithoutorgans.cc through a Cloudflare Tunnel.
# Run once on the machine that will host the site (Mac mini or Linux VPS). Interactive: opens a browser to log in to Cloudflare.
set -e
DOMAIN="${DOMAIN:-bodywithoutorgans.cc}"
NAME="${TUNNEL_NAME:-movies}"
PORT="${PORT:-8787}"

if ! command -v cloudflared >/dev/null; then
  if command -v brew >/dev/null; then brew install cloudflared
  elif command -v apt-get >/dev/null; then
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update && sudo apt-get install -y cloudflared
  else echo "install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; exit 1; fi
fi

[ -f "$HOME/.cloudflared/cert.pem" ] || cloudflared tunnel login
cloudflared tunnel list | grep -q " $NAME " || cloudflared tunnel create "$NAME"
ID=$(cloudflared tunnel list -o json | python3 -c "import json,sys; print([t['id'] for t in json.load(sys.stdin) if t['name']=='$NAME'][0])")
mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<CFG
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json
ingress:
  - hostname: $DOMAIN
    service: http://localhost:$PORT
  - hostname: www.$DOMAIN
    service: http://localhost:$PORT
  - service: http_status:404
CFG
cloudflared tunnel route dns -f "$NAME" "$DOMAIN"
cloudflared tunnel route dns -f "$NAME" "www.$DOMAIN"
if [ "$(uname)" = "Darwin" ]; then sudo cloudflared service install; else sudo cloudflared --config "$HOME/.cloudflared/config.yml" service install; sudo systemctl enable --now cloudflared; fi
echo "Tunnel '$NAME' → https://$DOMAIN (make sure the app is running on port $PORT)"
