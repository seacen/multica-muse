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
  # The new supervisor may take a few seconds to appear in pgrep (flock handoff
  # from a dying instance can delay it). Retry before declaring failure, to
  # avoid false FAILED entries like 2026-10-10 13:21:52.
  ok=0
  for _ in 1 2 3; do
    sleep 8
    if pgrep -f "[s]upervisor.sh" >/dev/null 2>&1; then
      ok=1
      break
    fi
  done
  if [ "$ok" = 1 ]; then
    log "supervisor restarted OK"
  else
    log "supervisor restart FAILED"
  fi
fi
