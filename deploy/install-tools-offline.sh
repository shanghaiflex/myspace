#!/bin/sh
# Installs a standalone Python, yt-dlp (zipapp) and cloudflared into the home directory. No sudo, no Xcode CLT, no Homebrew.
# Expects python.tar.gz, yt-dlp, cloudflared next to this script (downloaded elsewhere and copied over).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local" "$HOME/bin"
if [ ! -x "$HOME/.local/python312/bin/python3" ]; then
  rm -rf "$HOME/.local/python312"; mkdir -p "$HOME/.local/python312"
  tar xzf "$HERE/python.tar.gz" -C "$HOME/.local/python312" --strip-components=1
fi
cp "$HERE/yt-dlp" "$HOME/bin/yt-dlp.zip" 2>/dev/null || true
printf '#!/bin/sh\nexec "%s/.local/python312/bin/python3" "%s/bin/yt-dlp.zip" "$@"\n' "$HOME" "$HOME" > "$HOME/bin/yt-dlp"
cp "$HERE/cloudflared" "$HOME/bin/cloudflared"
chmod +x "$HOME/bin/yt-dlp" "$HOME/bin/cloudflared" "$HOME/.local/python312/bin/python3"
xattr -dr com.apple.quarantine "$HOME/bin/cloudflared" "$HOME/.local/python312" 2>/dev/null || true
"$HOME/.local/python312/bin/python3" -c "import ssl, sqlite3, json; print('python', __import__('sys').version.split()[0])"
"$HOME/bin/yt-dlp" --version
"$HOME/bin/cloudflared" --version
