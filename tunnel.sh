#!/usr/bin/env bash
# Restart the Cloudflare quick tunnel for Menu Markup (port 8000).
#   ./tunnel.sh        (re)start tunnel in background, print new public URL
#   ./tunnel.sh stop   stop tunnel
cd "$(dirname "$0")"
CF="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
command -v "$CF" >/dev/null || CF=cloudflared
[ -f tunnel.pid ] && kill "$(cat tunnel.pid)" 2>/dev/null; rm -f tunnel.pid
pkill -f "^[^ ]*cloudflared tunnel --no-autoupdate --url http://localhost:8000" 2>/dev/null
[ "$1" = "stop" ] && { echo "Tunnel stopped"; exit 0; }
# Make sure the app itself is up
curl -s -o /dev/null http://localhost:8000 || ./run.sh --bg
setsid nohup "$CF" tunnel --no-autoupdate --url http://localhost:8000 > tunnel.log 2>&1 < /dev/null & echo $! > tunnel.pid
for i in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -1)
  [ -n "$URL" ] && grep -q "Registered tunnel connection" tunnel.log && { echo "$URL" > tunnel_url.txt; echo "Tunnel running (pid $(cat tunnel.pid)): $URL"; echo "(New hostname may take ~30-60s to resolve on some DNS resolvers.)"; exit 0; }
  sleep 1
done
echo "Tunnel URL not found yet; check tunnel.log"; exit 1
