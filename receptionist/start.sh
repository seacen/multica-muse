#!/usr/bin/env bash
# Start the Multica Muse receptionist.
# Requires MUSE_RECEPTIONIST_TOKEN to be set (never invent a default).
set -euo pipefail

if [ -z "${MUSE_RECEPTIONIST_TOKEN:-}" ]; then
  echo "ERROR: MUSE_RECEPTIONIST_TOKEN is not set." >&2
  echo "Generate one, e.g.:  openssl rand -hex 32" >&2
  echo "Then:  export MUSE_RECEPTIONIST_TOKEN='<token>'" >&2
  exit 1
fi

exec python3 "$(dirname "$0")/server.py"
