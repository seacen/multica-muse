#!/usr/bin/env bash
# One-command setup: Multica daemon + Muse receptionist, wired together.
#
#   Selfhost:  ./install.sh --server https://multica.example.com --token mul_xxx
#   SaaS:      ./install.sh --saas --token mul_xxx
#
# --token is a Multica personal access token (mul_...). Create it in the
#   Multica web UI (avatar -> Settings -> API Tokens). Prefer passing it via
#   env so it never lands in shell history:
#     MULTICA_TOKEN=mul_xxx ./install.sh --server https://multica.example.com
#
# What it does:
#   1. installs the Muse receptionist (token, service, health check)
#   2. downloads the multica daemon binary (with the muse backend)
#   3. logs the daemon in (dedicated "muse" profile, doesn't touch yours)
#   4. starts the daemon with MUSE_ENDPOINT/MUSE_TOKEN wired up
#
# After this, ask your Muse to register the queue-watcher hook
# (hooks.add -> dry_run -> enable). Until then, tasks queue but don't execute.
#
# Env overrides:
#   DAEMON_RELEASE_TAG  GitHub release tag to download the daemon from
#                       (default: muse-backend-v2 on seacen/multica;
#                       after the upstream PR merges this flips to official)
#   DAEMON_REPO         owner/repo for the release (default: seacen/multica)
set -euo pipefail

RECEP_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$HOME/.config/multica-muse"
ENV_FILE="$CONF_DIR/receptionist.env"
DAEMON_DIR="$HOME/.local/bin"
DAEMON_BIN="$DAEMON_DIR/multica"
DAEMON_ENV="$CONF_DIR/daemon.env"
PROFILE="muse"

DAEMON_REPO="${DAEMON_REPO:-seacen/multica}"
DAEMON_RELEASE_TAG="${DAEMON_RELEASE_TAG:-muse-backend-v2}"

SERVER=""
TOKEN="${MULTICA_TOKEN:-}"
SAAS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2;;
    --saas) SAAS=1; shift;;
    --token) TOKEN="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

if [ "$SAAS" = "1" ]; then
  SERVER="https://api.multica.ai"
fi
if [ -z "$SERVER" ]; then
  echo "ERROR: need --server https://... or --saas" >&2; exit 1
fi
if [ -z "$TOKEN" ]; then
  echo "ERROR: need --token mul_... (or MULTICA_TOKEN env)." >&2
  echo "Create one in the Multica web UI: avatar -> Settings -> API Tokens." >&2
  exit 1
fi

echo "==> 1/5 receptionist"
mkdir -p "$CONF_DIR"
if [ ! -f "$ENV_FILE" ]; then
  printf 'MUSE_RECEPTIONIST_TOKEN=%s\n' "$(openssl rand -hex 32)" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "    token generated -> $ENV_FILE"
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
if ! curl -fsS --max-time 3 http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
  if systemctl --user daemon-reload 2>/dev/null; then
    mkdir -p "$HOME/.config/systemd/user"
    # B1: Template the service file with the actual install location.
    # The repo may be cloned anywhere; the unit must point at the real path.
    sed "s|@RECEP_DIR@|$RECEP_DIR|g" \
      "$RECEP_DIR/receptionist/multica-muse-receptionist.service" \
      > "$HOME/.config/systemd/user/multica-muse-receptionist.service"
    systemctl --user daemon-reload
    systemctl --user enable --now multica-muse-receptionist.service
  else
    pkill -f "receptionist/server.py" 2>/dev/null || true
    nohup "$RECEP_DIR/receptionist/start.sh" >/tmp/multica-muse-receptionist.log 2>&1 &
  fi
  sleep 2
fi
curl -fsS --max-time 5 http://127.0.0.1:8765/healthz >/dev/null \
  || { echo "ERROR: receptionist not responding" >&2; exit 1; }
echo "    receptionist OK on 127.0.0.1:8765"

echo "==> 2/5 daemon binary"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in x86_64) ARCH=amd64;; aarch64|arm64) ARCH=arm64;; esac
if [ "$OS" != "linux" ]; then
  echo "ERROR: this installer targets the Muse cloud computer (linux); got $OS" >&2
  exit 1
fi
ASSET="multica-${OS}-${ARCH}"
mkdir -p "$DAEMON_DIR"
# N3: Don't trust an existing binary — verify it supports the muse backend.
# Download to a temp file first, verify, then replace (avoids truncating
# a working install on failed download).
NEED_DOWNLOAD=1
if [ -x "$DAEMON_BIN" ]; then
  if "$DAEMON_BIN" agent list --help 2>/dev/null | grep -q "muse"; then
    echo "    existing binary supports muse, keeping it"
    NEED_DOWNLOAD=0
  else
    echo "    existing binary lacks muse support, upgrading"
  fi
fi
if [ "$NEED_DOWNLOAD" = "1" ]; then
  URL="https://github.com/${DAEMON_REPO}/releases/download/${DAEMON_RELEASE_TAG}/${ASSET}"
  echo "    downloading $URL"
  TMP_BIN="$DAEMON_BIN.tmp.$$"
  curl -fsSL --max-time 120 -o "$TMP_BIN" "$URL" \
    || { echo "ERROR: download failed" >&2; rm -f "$TMP_BIN"; exit 1; }
  chmod +x "$TMP_BIN"
  # Verify the downloaded binary actually runs before replacing
  if ! "$TMP_BIN" version >/dev/null 2>&1; then
    echo "ERROR: downloaded binary failed to run" >&2
    rm -f "$TMP_BIN"
    exit 1
  fi
  mv "$TMP_BIN" "$DAEMON_BIN"
fi
"$DAEMON_BIN" version | head -1

echo "==> 3/5 daemon login (profile: $PROFILE)"
if ! MULTICA_SERVER_URL="$SERVER" "$DAEMON_BIN" login --token "$TOKEN" --profile "$PROFILE" >/dev/null 2>&1; then
  # token via flag may need the space form; retry verbosely on failure
  MULTICA_SERVER_URL="$SERVER" "$DAEMON_BIN" login --token "$TOKEN" --profile "$PROFILE" \
    || { echo "ERROR: daemon login failed (bad token or unreachable server?)" >&2; exit 1; }
fi
echo "    logged in to $SERVER"

echo "==> 4/5 wire daemon <-> receptionist"
MUSE_TOKEN="$(grep -o 'MUSE_RECEPTIONIST_TOKEN=.*' "$ENV_FILE" | cut -d= -f2)"
printf 'MUSE_ENDPOINT=http://127.0.0.1:8765\nMUSE_TOKEN=%s\n' "$MUSE_TOKEN" > "$DAEMON_ENV"
chmod 600 "$DAEMON_ENV"
echo "    env written -> $DAEMON_ENV"

echo "==> 5/5 start daemon"
set -a; . "$DAEMON_ENV"; set +a
"$DAEMON_BIN" daemon stop --profile "$PROFILE" >/dev/null 2>&1 || true
"$DAEMON_BIN" daemon start --profile "$PROFILE"
sleep 3
# N3: Verify the daemon actually started; don't declare success on failure.
if ! "$DAEMON_BIN" daemon status --profile "$PROFILE" 2>&1 | head -5; then
  echo "ERROR: daemon failed to start (see output above)" >&2
  exit 1
fi
echo "    daemon running"

echo ""
echo "Done. The daemon registered a 'muse' runtime on $SERVER."
echo "Next steps:"
echo "  1. Ask your Muse to register the queue-watcher hook (hooks.add -> dry_run -> enable)."
echo "  2. Ask your Muse to create the dedicated side chat for Multica notifications."
echo "  3. In Multica, create an agent on the 'muse' runtime and dispatch a task."
