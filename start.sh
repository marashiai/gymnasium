#!/usr/bin/env bash
# Boot the Gymnasium University web app on localhost and OPTIONALLY a
# Cloudflare named tunnel that publishes it at gymnasium.marashi.ai.
#
# Tunnel mode is auto-enabled when BOTH `TUNNEL_NAME` and `APP_HOST` are
# set (typically via `.env`, which is sourced automatically below).
# Without them the script behaves like a plain localhost launcher, so the
# same script works for local dev and for public hosting.
#
# SECURITY: the app authenticates with PLAINTEXT credentials over the
# login gate only. In tunnel mode it becomes publicly reachable, so the
# login gate is the sole protection — use a strong, unique password and
# consider putting Cloudflare Access in front (see docs/CLOUDFLARE_TUNNEL.md).
#
# Env overrides (all optional, .env wins over shell defaults):
#   PORT          default 8000   local port the app + tunnel ingress use
#   DB            default data/gymnasium.db
#   REPORTS       default reports
#   DOCS          default data/documents
#   OPENCODE_BIN  passthrough to the app (the opencode binary to drive AI)
#   SKIP_DNS=1    skip `cloudflared tunnel route dns` (after the first run,
#                 or when DNS is managed by hand)
#
# Tunnel-mode env (BOTH required to engage the tunnel):
#   TUNNEL_NAME   e.g. gymnasium
#   APP_HOST      e.g. gymnasium.marashi.ai
#   DOMAIN        default marashi.ai (informational / shared with reset-tunnel.sh)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Load .env so dev/prod overrides apply automatically. Variables already
# exported in the shell win over .env (set -a only exports newly assigned
# names).
if [ -f "${ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT}/.env"
  set +a
fi

PORT="${PORT:-8000}"
DB="${DB:-data/gymnasium.db}"
REPORTS="${REPORTS:-reports}"
DOCS="${DOCS:-data/documents}"
DOMAIN="${DOMAIN:-marashi.ai}"
export OPENCODE_BIN="${OPENCODE_BIN:-opencode}"

# Tunnel mode: only engage when both the tunnel name and the public
# hostname are set. A partial config almost always means the user forgot
# one, but leaving either unset is also the documented way to run
# local-only — so we simply require both to opt in.
TUNNEL_MODE=0
if [ -n "${TUNNEL_NAME:-}" ] && [ -n "${APP_HOST:-}" ]; then
  TUNNEL_MODE=1
fi

# Prefer the project venv (which has markitdown for PDF→markdown conversion),
# then an installed console script on PATH, else fall back to running the
# package module directly so the script works from a source checkout too.
if [ -x "${ROOT}/.venv/bin/gymnasium" ]; then
  APP_CMD=("${ROOT}/.venv/bin/gymnasium")
elif command -v gymnasium >/dev/null 2>&1; then
  APP_CMD=(gymnasium)
else
  APP_CMD=(python3 -m university.server)
fi

RUNTIME_DIR="${ROOT}/.runtime"
LOG_DIR="${ROOT}/logs"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

# Pre-flight cleanup: clear any stale listener on the app port and any
# leftover cloudflared from a previous run, so the new process wins the
# port-bind race.
pids="$(lsof -t -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
[ -n "$pids" ] && kill $pids 2>/dev/null || true
if [ "$TUNNEL_MODE" = "1" ]; then
  pkill -f "cloudflared tunnel .* run ${TUNNEL_NAME}" 2>/dev/null || true
fi
sleep 0.5

# Tunnel pre-flight: log-in check, tunnel create-if-missing, DNS route,
# generated ingress config.
TUNNEL_CONFIG=""
TUNNEL_UUID=""
if [ "$TUNNEL_MODE" = "1" ]; then
  command -v cloudflared >/dev/null || { echo "cloudflared not on PATH" >&2; exit 1; }
  if [ ! -f "${HOME}/.cloudflared/cert.pem" ]; then
    echo "cloudflared is not logged in. Run:" >&2
    echo "  cloudflared tunnel login" >&2
    exit 1
  fi

  # Idempotent — `tunnel create` errors if it already exists, but we only
  # need the UUID afterwards regardless.
  cloudflared tunnel create "$TUNNEL_NAME" 2>/dev/null || true

  TUNNEL_UUID="$(
    cloudflared tunnel list --output json 2>/dev/null \
    | TUNNEL_NAME="$TUNNEL_NAME" python3 -c "
import json, os, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for t in data:
    if t.get('name') == os.environ['TUNNEL_NAME']:
        print(t.get('id') or t.get('ID') or '')
        break
"
  )"
  if [ -z "$TUNNEL_UUID" ]; then
    TUNNEL_UUID="$(cloudflared tunnel list 2>/dev/null \
      | awk -v n="$TUNNEL_NAME" '$2==n {print $1; exit}')"
  fi
  [ -n "$TUNNEL_UUID" ] || { echo "could not resolve tunnel UUID for ${TUNNEL_NAME}" >&2; exit 1; }

  CRED_FILE="${HOME}/.cloudflared/${TUNNEL_UUID}.json"
  if [ ! -f "$CRED_FILE" ]; then
    echo "missing credentials file: $CRED_FILE" >&2
    echo "The local tunnel credentials may be stale or missing. Run:" >&2
    echo "  ./reset-tunnel.sh" >&2
    exit 1
  fi

  # DNS — idempotent at the cloudflared level. A no-op once the CNAME
  # already points at this tunnel UUID.
  if [ -z "${SKIP_DNS:-}" ]; then
    cloudflared tunnel route dns "$TUNNEL_NAME" "$APP_HOST" || true
  fi

  TUNNEL_CONFIG="${RUNTIME_DIR}/cloudflared.yml"
  cat > "$TUNNEL_CONFIG" <<YAML
tunnel: ${TUNNEL_NAME}
credentials-file: ${CRED_FILE}

ingress:
  - hostname: ${APP_HOST}
    service: http://localhost:${PORT}
  - service: http_status:404
YAML
fi

APP_PID=""
CLOUDFLARED_PID=""
cleanup() {
  trap - INT TERM EXIT
  if [ -n "$CLOUDFLARED_PID" ]; then
    kill "$CLOUDFLARED_PID" 2>/dev/null || true
  fi
  if [ -n "$APP_PID" ]; then
    kill "$APP_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Boot the app in the background, then wait until it answers locally
# before wiring up the tunnel ingress.
"${APP_CMD[@]}" --host 127.0.0.1 --port "$PORT" --db "$DB" \
  --reports "$REPORTS" --docs-dir "$DOCS" --ingest-on-start \
  > "${LOG_DIR}/gymnasium.log" 2>&1 &
APP_PID=$!

ready=0
for _ in $(seq 1 50); do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "app exited during startup; see ${LOG_DIR}/gymnasium.log" >&2
    exit 1
  fi
  if curl -fsS -o /dev/null "http://localhost:${PORT}/"; then
    ready=1
    break
  fi
  sleep 0.2
done
if [ "$ready" != "1" ]; then
  echo "app did not answer on http://localhost:${PORT}/ in time; see ${LOG_DIR}/gymnasium.log" >&2
  exit 1
fi

# Spawn cloudflared after the app is serving so the ingress target is up
# by the time external traffic arrives.
if [ "$TUNNEL_MODE" = "1" ]; then
  cloudflared tunnel --config "$TUNNEL_CONFIG" run "$TUNNEL_NAME" \
    > "${LOG_DIR}/cloudflared.log" 2>&1 &
  CLOUDFLARED_PID=$!
fi

if [ "$TUNNEL_MODE" = "1" ]; then
  echo "gymnasium -> https://${APP_HOST}    (local http://localhost:${PORT})"
  echo "cloudflared -> ${LOG_DIR}/cloudflared.log"
else
  echo "gymnasium -> http://localhost:${PORT}    (logs: ${LOG_DIR}/gymnasium.log)"
fi

# Block on the app; the trap tears down cloudflared on exit.
wait "$APP_PID"
