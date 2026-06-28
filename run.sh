#!/usr/bin/env bash
# Activate the project venv (which has markitdown for PDF→markdown conversion)
# then hand off to start.sh. Keeps the venv activation out of start.sh so the
# start script stays runnable from a source checkout without a venv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "${ROOT}/.venv/bin/activate" ]; then
  echo "no .venv found at ${ROOT}/.venv; create one with:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e .[dev]" >&2
  exit 1
fi

# shellcheck disable=SC1091
. "${ROOT}/.venv/bin/activate"

exec "${ROOT}/start.sh" "$@"