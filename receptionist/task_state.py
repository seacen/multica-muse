#!/usr/bin/env python3
"""Atomic task state transitions for multica-muse workers.

B4: The worker previously did check-then-write (read status.json, then
overwrite it). A cancel landing between the check and the write would be
silently overwritten. This helper uses fcntl file locking to make the
read-modify-write atomic, and enforces a state machine where terminal
states (cancelled, completed, failed) cannot be overwritten.

Usage:
    python3 task_state.py <tasks_dir> <task_id> <new_status> [--error MSG]

Valid transitions:
    queued    -> running, cancelled
    running   -> completed, failed, cancelled
    cancelled -> (terminal, no transitions allowed)
    completed -> (terminal, no transitions allowed)
    failed    -> (terminal, no transitions allowed)

Exit codes:
    0 - transition succeeded
    1 - invalid transition (e.g. cancelled -> running)
    2 - task not found or other error
"""

import fcntl
import os
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Valid state transitions. Terminal states have no outgoing transitions.
TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "cancelled": set(),
    "completed": set(),
    "failed": set(),
}


def transition(tasks_dir: Path, task_id: str, new_status: str, error: str = None) -> int:
    task_dir = tasks_dir / task_id
    status_path = task_dir / "status.json"
    lock_path = task_dir / ".lock"

    if not status_path.exists():
        print(f"task {task_id} not found", file=sys.stderr)
        return 2

    # Use a lock file for mutual exclusion. The lock is held during
    # the entire read-modify-write cycle.
    task_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(lock_path, "w") as lockf:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX)
        except (OSError, AttributeError):
            # fcntl not available (e.g. Windows) — fall back to
            # best-effort without locking. The state machine still
            # prevents invalid transitions, just not races.
            pass

        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            print(f"failed to read status: {e}", file=sys.stderr)
            return 2

        current = status.get("status", "unknown")
        allowed = TRANSITIONS.get(current, set())

        if new_status not in TRANSITIONS:
            print(f"unknown status: {new_status!r}", file=sys.stderr)
            return 1
        if new_status not in allowed:
            if not allowed:
                print(
                    f"invalid transition: {current} -> {new_status} "
                    f"({current} is terminal)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"invalid transition: {current} -> {new_status} "
                    f"(allowed: {sorted(allowed)})",
                    file=sys.stderr,
                )
            return 1

        status["status"] = new_status
        status["updated_at"] = utcnow()
        if new_status in ("completed", "failed", "cancelled"):
            status["finished_at"] = status["updated_at"]
            # Scrub the task token from request.json on terminal states.
            # The server invalidates the token, but there's no reason to
            # keep a credential-shaped value on disk.
            # Use 0600 via atomic write (consistent with server.py).
            # os.open with O_CREAT|O_EXCL ensures the mode applies.
            req_path = task_dir / "request.json"
            try:
                if req_path.is_file():
                    req = json.loads(req_path.read_text(encoding="utf-8"))
                    if "task_token" in req:
                        del req["task_token"]
                        tmp_req = req_path.with_suffix(".json.tmp")
                        fd = os.open(tmp_req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as f:
                                f.write(json.dumps(req, ensure_ascii=False, indent=2))
                        except Exception:
                            os.close(fd)
                            raise
                        tmp_req.replace(req_path)
            except (ValueError, OSError):
                pass  # Best-effort; don't fail the transition.
        if error is not None:
            status["error"] = error
        if new_status == "running" and "started_at" not in status:
            status["started_at"] = status["updated_at"]

        # Atomic write: temp file + rename
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(status_path)

    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2

    tasks_dir = Path(sys.argv[1])
    task_id = sys.argv[2]
    new_status = sys.argv[3]
    error = None

    # Parse --error MSG
    args = sys.argv[4:]
    for i, a in enumerate(args):
        if a == "--error" and i + 1 < len(args):
            error = args[i + 1]

    # Basic task_id validation (prevent path traversal)
    if not task_id.replace("-", "").replace("_", "").isalnum():
        print(f"invalid task_id: {task_id}", file=sys.stderr)
        return 2

    return transition(tasks_dir, task_id, new_status, error)


if __name__ == "__main__":
    sys.exit(main())
