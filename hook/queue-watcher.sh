#!/usr/bin/env bash
# Hook detector: watch the Multica Muse task queue and wake one worker
# agent per queued task.
#
# Install: copy to ~/hooks/scripts/multica-muse-queue.sh, then
#   hooks.add --id multica-muse-queue \
#     --script-path ~/hooks/scripts/multica-muse-queue.sh \
#     --prompt "$(cat worker-prompt.md)" --poll-interval-secs 20
# then hooks.dry_run, then hooks.enable.
#
# Protocol: ends with exactly one silent/wake (explicit `exit 0` after each
# as defense-in-depth). Never claims the task here; the worker claims
# atomically (rename), so a duplicate wake is harmless (the loser exits
# quietly).
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

QUEUE="$HOME/workspace/multica-muse/queue"
STATE_DIR="$HOME/hooks/state"
STATE_FILE="$STATE_DIR/multica-muse-queue.json"
REWAKE_AFTER_SECS=180
DRY="${HATCH_HOOK_DRY_RUN:-0}"

mkdir -p "$QUEUE"

shopt -s nullglob
files=("$QUEUE"/*.json)
if [ "${#files[@]}" -eq 0 ]; then
  silent "multica-muse queue empty" '{}'
  exit 0
fi

# Oldest queued task first (one wake per poll keeps workers serialized).
# files[@] is non-empty here, so ls never falls back to listing the cwd.
oldest="$(ls -1tr "${files[@]}" | head -n 1)"
task_id="$(basename "$oldest" .json)"
now="$(date +%s)"

last_id=""
last_at=0
if [ -f "$STATE_FILE" ]; then
  last_id="$(jq -r '.last_wake_task_id // ""' "$STATE_FILE" 2>/dev/null || true)"
  last_at="$(jq -r '.last_wake_at // 0' "$STATE_FILE" 2>/dev/null || true)"
fi

if [ "$task_id" = "$last_id" ] && [ "$((now - last_at))" -lt "$REWAKE_AFTER_SECS" ]; then
  silent "task $task_id already woken $((now - last_at))s ago; worker claiming" \
    "{\"task_id\":\"$task_id\"}"
  exit 0
fi

# Record the wake (skipped on dry runs so they don't consume detections).
if [ "$DRY" != "1" ]; then
  mkdir -p "$STATE_DIR"
  jq -n --arg id "$task_id" --argjson at "$now" \
    '{last_wake_task_id:$id, last_wake_at:$at}' > "$STATE_FILE.tmp" \
    && mv "$STATE_FILE.tmp" "$STATE_FILE"
fi

wake "multica task queued: $task_id" "{\"task_id\":\"$task_id\"}"
exit 0
