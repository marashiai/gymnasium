#!/usr/bin/env bash
# Build and start Gymnasium's app + optional Cloudflare tunnel containers.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ -f "${ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT}/.env"
  set +a
fi

export GYMNASIUM_DATA_DIR="${GYMNASIUM_DATA_DIR:-${ROOT}/data}"
export GYMNASIUM_REPORTS_DIR="${GYMNASIUM_REPORTS_DIR:-${ROOT}/reports}"
export OPENCODE_DATA_DIR="${OPENCODE_DATA_DIR:-${HOME}/.gymnasium-opencode}"
export CLOUDFLARED_DIR="${CLOUDFLARED_DIR:-${HOME}/.cloudflared}"

mkdir -p \
  "$GYMNASIUM_DATA_DIR/documents" \
  "$GYMNASIUM_REPORTS_DIR" \
  "$OPENCODE_DATA_DIR"

security_options="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null)" || {
  echo "cannot reach the Docker daemon; select the rootless context first" >&2
  echo "  docker context use rootless" >&2
  exit 1
}
case "$security_options" in
  *rootless*) ;;
  *)
    echo "refusing to start with a non-rootless Docker daemon" >&2
    echo "  docker context use rootless" >&2
    exit 1
    ;;
esac

compose_args=()
if [ -n "${TUNNEL_NAME:-}" ] || [ -n "${APP_HOST:-}" ]; then
  [ -n "${TUNNEL_NAME:-}" ] && [ -n "${APP_HOST:-}" ] || {
    echo "TUNNEL_NAME and APP_HOST must be set together" >&2
    exit 1
  }
  [ -f "$CLOUDFLARED_DIR/cert.pem" ] || {
    echo "missing $CLOUDFLARED_DIR/cert.pem" >&2
    exit 1
  }
  compose_args=(--profile tunnel)
fi

if [ "${1:-}" = "--detach" ] || [ "${1:-}" = "-d" ]; then
  exec docker compose "${compose_args[@]}" up \
    --detach --build --wait --wait-timeout 180
fi

exec docker compose "${compose_args[@]}" up --build
