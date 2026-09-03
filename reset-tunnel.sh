#!/usr/bin/env bash
#
# Reset the Cloudflare tunnel from the same image used in production.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Same .env auto-load as start.sh so a single config drives both.
if [ -f "${ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT}/.env"
  set +a
fi

: "${TUNNEL_NAME:?must be set in .env}"
: "${APP_HOST:?must be set in .env}"

export CLOUDFLARED_DIR="${CLOUDFLARED_DIR:-${HOME}/.cloudflared}"
[ -f "$CLOUDFLARED_DIR/cert.pem" ] || {
  echo "missing $CLOUDFLARED_DIR/cert.pem" >&2
  exit 1
}

exec docker compose --profile tunnel run --rm --no-deps \
  -e RESET_TUNNEL=1 -e ROUTE_ONLY=1 cloudflared
