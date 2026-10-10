#!/usr/bin/env bash
# Backstop watchdog for the Multica Muse integration (run via cron every 2m).
# Ensures the always-on supervisor is running; the supervisor itself keeps
# the receptionist and daemon alive on a 5s loop.
# This survives VM replacement because cron is runtime-managed.
set -uo pipefail
export TZ=UTC

LOG="$HOME/workspace/multica-muse/watchdog.log"
SUPERVISOR="$HOME/workspace/multica-muse/supervisor.sh"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

if ! pgrep -f "[s]upervisor.sh" >/dev/null 2>&1; then
  log "supervisor down -> starting"
  chmod +x "$SUPERVISOR"
  nohup bash "$SUPERVISOR" >> "$HOME/workspace/multica-muse/supervisor.out.log" 2>&1 &
  sleep 8
  if pgrep -f "[s]upervisor.sh" >/dev/null 2>&1; then
    log "supervisor restarted OK"
  else
    log "supervisor restart FAILED"
  fi
fi
