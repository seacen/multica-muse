#!/usr/bin/env bash
# Always-on supervisor for the Multica Muse integration.
# Keeps the receptionist and the muse-backend daemon alive, respawning
# within seconds on crash. Single instance via flock.
# The cron watchdog (every 2m) is the backstop: it restarts THIS supervisor
# if the VM was replaced or the supervisor itself died.
set -uo pipefail
export TZ=UTC

BIN="$HOME/workspace/multica-muse/bin/multica"
RECEPTIONIST_DIR="$HOME/workspace/multica-muse/receptionist"
LOG="$HOME/workspace/multica-muse/supervisor.log"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# single instance
exec 9>"$HOME/workspace/multica-muse/supervisor.lock"
if ! flock -n 9; then
  echo "supervisor already running"
  exit 0
fi

ensure_receptionist() {
  if ! curl -sf -m2 http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
    log "receptionist down -> starting"
    set -a; source "$HOME/.config/multica-muse/receptionist.env"; set +a
    nohup bash "$RECEPTIONIST_DIR/start.sh" >> "$HOME/.config/multica-muse/receptionist.out.log" 2>&1 9>&- &
  fi
}

daemon_healthy() {
  # pgrep is the primary probe: process existence = alive.
  # `daemon status` has intermittent false negatives (longer than the
  # 2s retry window), so it's only the secondary confirmation.
  if pgrep -f "bin/multica daemon start.*--profile muse" >/dev/null 2>&1; then
    return 0
  fi
  "$BIN" daemon status --profile muse 2>/dev/null | grep -q "running"
}

ensure_daemon() {
  # After we start one, give it 20s grace before the next check so a slow
  # startup doesn't trigger a duplicate launch (that caused multi-daemon
  # fights on 2026-10-10).
  # `daemon status` has ~4% intermittent false negatives (2026-10-10:
  # 362 false "down" in 16h). Retry once after 2s; only declare dead
  # after two consecutive failures.
  if ! daemon_healthy; then
    sleep 2
    if daemon_healthy; then
      return 0
    fi
    log "daemon down (confirmed) -> starting"
    set -a; source "$HOME/.config/multica-muse/daemon.env"; set +a
    nohup "$BIN" daemon start --profile muse >> "$HOME/.multica/profiles/muse/daemon.out.log" 2>&1 9>&- &
    sleep 20
  fi
}

log "supervisor started (pid $$)"
while true; do
  ensure_receptionist
  ensure_daemon
  sleep 5
done
