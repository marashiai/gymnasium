#!/usr/bin/env bash
set -euo pipefail

: "${TUNNEL_NAME:?must be set}"
: "${APP_HOST:?must be set}"

find_tunnel() {
  require_credentials="$1"
  cloudflared tunnel list --output json 2>/dev/null \
    | TUNNEL_NAME="$TUNNEL_NAME" \
      REQUIRE_CREDENTIALS="$require_credentials" python3 -c '
import json
import os
from pathlib import Path
import sys

try:
    tunnels = json.load(sys.stdin)
except Exception:
    sys.exit(0)

for tunnel in tunnels:
    if tunnel.get("name") == os.environ["TUNNEL_NAME"]:
        tunnel_id = tunnel.get("id") or tunnel.get("ID") or ""
        credentials = Path("/etc/cloudflared") / f"{tunnel_id}.json"
        if os.environ["REQUIRE_CREDENTIALS"] != "1" or credentials.is_file():
            print(tunnel_id)
            break
'
}

select_existing_tunnel() {
  require_credentials="$1"
  for cert in /etc/cloudflared/*.pem; do
    [ -f "$cert" ] || continue
    export TUNNEL_ORIGIN_CERT="$cert"
    TUNNEL_UUID="$(find_tunnel "$require_credentials")"
    if [ -n "$TUNNEL_UUID" ]; then
      return 0
    fi
  done
  return 1
}

TUNNEL_UUID=""
if [ -n "${RESET_TUNNEL:-}" ]; then
  if select_existing_tunnel 0; then
    cloudflared tunnel delete "$TUNNEL_UUID"
  fi
  TUNNEL_UUID=""
fi

if ! select_existing_tunnel 1; then
  if select_existing_tunnel 0; then
    echo "missing /etc/cloudflared/${TUNNEL_UUID}.json" >&2
    exit 1
  fi

  export TUNNEL_ORIGIN_CERT="${TUNNEL_ORIGIN_CERT:-/etc/cloudflared/cert.pem}"
  [ -f "$TUNNEL_ORIGIN_CERT" ] || {
    echo "missing Cloudflare origin certificate" >&2
    exit 1
  }
  cloudflared tunnel create "$TUNNEL_NAME"
  select_existing_tunnel 1 || {
    echo "could not resolve tunnel UUID for ${TUNNEL_NAME}" >&2
    exit 1
  }
fi

CREDENTIALS_FILE="/etc/cloudflared/${TUNNEL_UUID}.json"

if [ -z "${SKIP_DNS:-}" ]; then
  route_args=()
  [ -z "${RESET_TUNNEL:-}" ] || route_args=(-f)
  cloudflared tunnel route dns "${route_args[@]}" "$TUNNEL_UUID" "$APP_HOST"
fi

if [ -n "${ROUTE_ONLY:-}" ]; then
  exit 0
fi

cat > /tmp/config.yml <<YAML
tunnel: ${TUNNEL_UUID}
credentials-file: ${CREDENTIALS_FILE}
protocol: http2

ingress:
  - hostname: ${APP_HOST}
    service: http://app:8000
  - service: http_status:404
YAML

exec cloudflared tunnel --config /tmp/config.yml run "$TUNNEL_UUID"
