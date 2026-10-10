#!/usr/bin/env python3
"""Multica Muse receptionist.

A tiny local HTTP API that lets the Multica `muse` backend delegate task
execution to a Muse agent run. Stdlib only.

Flow:
  1. Go backend -> POST /v1/execute {prompt, session_id?}  (task queued)
     session_id is "muse:<prev_task_id>" when resuming; the previous
     task's result.md is injected into the prompt as session context
     (Hermes-style resume via context injection, since hook workers
     cannot resume a dead agent session).
  2. A hook's polling script sees queue/<id>.json and wakes a worker agent.
  3. Worker claims the task (atomic rename), runs the prompt with full
     capabilities, appends events to tasks/<id>/events.jsonl, writes
     tasks/<id>/result.md + status.json.
  4. Go backend polls GET /v1/tasks/<id> and /v1/tasks/<id>/events.

All endpoints require `Authorization: Bearer <token>` except /healthz.
The token MUST come from the MUSE_RECEPTIONIST_TOKEN environment variable;
the server refuses to start without it. Binds 127.0.0.1 by default.
"""

import hashlib
import hmac
import json
import threading
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = "1.4.0"
ROOT = Path.home() / "workspace" / "multica-muse"
QUEUE_DIR = ROOT / "queue"
TASKS_DIR = ROOT / "tasks"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_BODY_BYTES = 1_048_576  # 1 MiB cap on request bodies
MAX_RESULT_BYTES = 204_800  # 200 KiB cap on result.md served via API


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    # N2: Sensitive files (task_token in queue/request JSON) must be
    # owner-only. Don't rely on the caller's umask.
    # R1: os.fdopen takes ownership of fd — it closes on exception.
    # Don't close again in except (EBADF would mask the real error).
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
    except:
        # Clean up the temp file, but don't touch fd (fdopen owns it).
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


# Prefix the Go muse backend puts on the session id it reports
# (Result.SessionID = "muse:" + task_id). When the daemon resumes a
# session, it passes that value back as POST /v1/execute's session_id.
MUSE_SESSION_PREFIX = "muse:"
# Cap on injected previous-session context, to bound prompt growth.
MAX_PREV_CONTEXT_CHARS = 4000
def load_previous_session_context(session_id) -> str | None:
    """Return the previous task's result summary for session resume.

    session_id has the form "muse:<task_id>" (set by the Go backend).
    We read that task's result.md so the new worker starts with the
    prior turn's outcome — the poor-man's equivalent of Hermes'
    ACP session/resume, since hook workers can't resume a dead session.
    Returns None when there is nothing usable to inject.
    """
    if not isinstance(session_id, str) or not session_id.startswith(MUSE_SESSION_PREFIX):
        return None
    prev_task_id = session_id[len(MUSE_SESSION_PREFIX):]
    if not TASK_ID_RE.fullmatch(prev_task_id):
        return None
    result_path = TASKS_DIR / prev_task_id / "result.md"
    try:
        if not result_path.is_file():
            return None
        content = result_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content:
        return None
    if len(content) > MAX_PREV_CONTEXT_CHARS:
        content = content[:MAX_PREV_CONTEXT_CHARS] + "\n\n[truncated]"
    return content


def augment_prompt_with_session(prompt: str, session_id, prev_context: str | None) -> str:
    """Prepend previous-session context to the prompt for resume."""
    if not prev_context:
        return prompt
    return (
        "## Previous session context\n"
        f"This task continues session {session_id}. "
        "Below is the summary of what was done in the previous turn — "
        "build on it, do not repeat completed work:\n\n"
        f"{prev_context}\n\n"
        "---\n\n"
        "## Current task\n"
        f"{prompt}"
    )


def json_response(handler: BaseHTTPRequestHandler, code: int, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Receptionist(BaseHTTPRequestHandler):
    server_version = f"multica-muse-receptionist/{VERSION}"

    # -- helpers ---------------------------------------------------------
    def _token_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        presented = auth[len("Bearer "):].strip()
        expected = self.server.expected_token  # set on server instance
        return hmac.compare_digest(
            hashlib.sha256(presented.encode()).hexdigest(),
            hashlib.sha256(expected.encode()).hexdigest(),
        )

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "invalid Content-Length"
        if length <= 0 or length > MAX_BODY_BYTES:
            return None, "body missing or too large"
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "body is not valid JSON"
        # N5: Must be a JSON object, not an array or primitive.
        # Without this, body.get() raises AttributeError and the
        # connection drops instead of returning 400.
        if not isinstance(body, dict):
            return None, "body must be a JSON object"
        return body, None

    def _task_dir(self, task_id: str) -> Path:
        return TASKS_DIR / task_id

    def _load_status(self, task_id: str):
        p = self._task_dir(task_id) / "status.json"
        if not p.exists():
            return None
        try:
            status = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
        # B5: Reclaim orphaned tasks. If a task has been "running" with no
        # update for longer than its timeout (or 1 hour default), the worker
        # is gone. Mark it failed so it doesn't sit in fake "running" forever.
        # We don't auto-retry: without idempotency guarantees, re-running
        # could cause duplicate side effects.
        if status.get("status") == "running":
            updated = status.get("updated_at", "")
            timeout_s = status.get("timeout_s") or 3600
            try:
                # Parse ISO timestamp
                from datetime import datetime, timezone
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                age_s = (now_dt - updated_dt).total_seconds()
                if age_s > timeout_s:
                    status["status"] = "failed"
                    status["error"] = f"worker lost: no update for {int(age_s)}s (timeout {timeout_s}s)"
                    status["finished_at"] = utcnow()
                    status["updated_at"] = status["finished_at"]
                    atomic_write(p, json.dumps(status, ensure_ascii=False, indent=2))
                    self.log_message("reclaimed orphaned task %s after %ds", task_id, int(age_s))
            except (ValueError, TypeError):
                pass  # If we can't parse the timestamp, leave it alone
        return status

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            return json_response(self, 200, {"ok": True, "version": VERSION})
        if not self._token_ok():
            return json_response(self, 401, {"error": "unauthorized"})
        if path == "/v1/health":
            # Probed by the Go backend's ProbeMuseReceptionist (the muse
            # equivalent of CLI --version detection). Auth-required like
            # every other /v1 endpoint.
            return json_response(self, 200, {"protocol_version": 1, "version": VERSION})
        m = re.fullmatch(r"/v1/tasks/([^/]+)", path)
        if m:
            return self._get_task(m.group(1))
        m = re.fullmatch(r"/v1/tasks/([^/]+)/events", path)
        if m:
            qs = parse_qs(parsed.query)
            since = 0
            if "since" in qs:
                try:
                    since = int(qs["since"][0])
                except ValueError:
                    return json_response(self, 400, {"error": "invalid since"})
            return self._get_events(m.group(1), since)
        return json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._token_ok():
            return json_response(self, 401, {"error": "unauthorized"})
        if path == "/v1/execute":
            return self._execute()
        m = re.fullmatch(r"/v1/tasks/([^/]+)/cancel", path)
        if m:
            return self._cancel(m.group(1))
        return json_response(self, 404, {"error": "not found"})

    def log_message(self, fmt, *args):  # keep stdout logs tidy
        sys.stderr.write("%s %s\n" % (utcnow(), fmt % args))

    # -- endpoints ---------------------------------------------------------
    def _execute(self):
        body, err = self._read_json_body()
        if err:
            return json_response(self, 400, {"error": err})
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return json_response(self, 400, {"error": "prompt must be a non-empty string"})
        session_id = body.get("session_id")
        timeout_s = body.get("timeout_s")
        if session_id is not None and not isinstance(session_id, str):
            return json_response(self, 400, {"error": "session_id must be a string"})
        if timeout_s is not None:
            # N5: bool is a subclass of int; reject it explicitly.
            # Also reject negative, NaN, and infinite values.
            import math
            if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
                return json_response(self, 400, {"error": "timeout_s must be a number"})
            if not math.isfinite(timeout_s) or timeout_s < 0:
                return json_response(self, 400, {"error": "timeout_s must be a non-negative finite number"})
        workdir = body.get("workdir")
        if workdir is not None and not isinstance(workdir, str):
            return json_response(self, 400, {"error": "workdir must be a string"})
        task_token = body.get("task_token")
        if task_token is not None and not isinstance(task_token, str):
            return json_response(self, 400, {"error": "task_token must be a string"})
        # task_token is the task-scoped Multica API credential (mat_...)
        # forwarded by the Go muse backend from daemon ExecOptions.TaskToken.
        # It is handed to the worker (via request.json) so the worker can
        # run `multica` CLI with MULTICA_TOKEN=<task_token>, passing the
        # CLI's daemon-context check — the same identity CLI backends get.
        # Task-scoped and short-lived; never logged.
        server_url = body.get("server_url")
        if server_url is not None and not isinstance(server_url, str):
            return json_response(self, 400, {"error": "server_url must be a string"})
        workspace_id = body.get("workspace_id")
        if workspace_id is not None and not isinstance(workspace_id, str):
            return json_response(self, 400, {"error": "workspace_id must be a string"})
        # workdir is the daemon-prepared task working directory. The daemon
        # and this receptionist must share a filesystem (supported
        # deployment: both on this host); the worker runs the agent with
        # this directory so file-relative task context (AGENTS.md etc.)
        # resolves.

        task_id = "mt-" + uuid.uuid4().hex[:16]
        now = utcnow()
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=False, mode=0o700)

        # prompt_preview is computed from the original prompt so the
        # injected session context doesn't crowd it out.
        prompt_preview = prompt.strip()[:200]
        prev_context = load_previous_session_context(session_id)
        queued_prompt = augment_prompt_with_session(prompt, session_id, prev_context)

        status = {
            "task_id": task_id,
            "status": "queued",
            "session_id": session_id,
            "timeout_s": timeout_s,
            "workdir": workdir,
            "prompt_preview": prompt_preview,
            "resumed_from": session_id,
            "created_at": now,
            "updated_at": now,
        }
        atomic_write(task_dir / "status.json", json.dumps(status, ensure_ascii=False, indent=2))
        (task_dir / "events.jsonl").write_text("", encoding="utf-8")

        queue_payload = {
            "task_id": task_id,
            "prompt": queued_prompt,
            "session_id": session_id,
            "timeout_s": timeout_s,
            "workdir": workdir,
            "task_token": task_token,
            "server_url": server_url,
            "workspace_id": workspace_id,
            "created_at": now,
        }
        atomic_write(QUEUE_DIR / f"{task_id}.json",
                     json.dumps(queue_payload, ensure_ascii=False))

        self.log_message("queued task %s (session=%s)", task_id, session_id)
        # Go-B3: Include protocol_version so the Go backend can detect
        # version skew at submit time (probe may be stale).
        return json_response(self, 200, {"task_id": task_id, "status": "queued", "protocol_version": 1})

    def _get_task(self, task_id: str):
        if not TASK_ID_RE.fullmatch(task_id):
            return json_response(self, 400, {"error": "invalid task id"})
        status = self._load_status(task_id)
        if status is None:
            return json_response(self, 404, {"error": "unknown task"})
        out = {
            "task_id": task_id,
            "status": status.get("status"),
            "created_at": status.get("created_at"),
            "updated_at": status.get("updated_at"),
        }
        for key in ("session_id", "finished_at", "error", "result_preview"):
            if status.get(key) is not None:
                out[key] = status[key]
        if status.get("status") in ("completed", "failed"):
            rp = self._task_dir(task_id) / "result.md"
            if rp.exists():
                if rp.stat().st_size <= MAX_RESULT_BYTES:
                    out["result"] = rp.read_text(encoding="utf-8")
                else:
                    # N1: Don't silently omit oversized results. Report it
                    # explicitly so the caller knows the result exists but
                    # wasn't returned — not the same as "no result".
                    out["error"] = f"result too large ({rp.stat().st_size} bytes > {MAX_RESULT_BYTES} byte limit); read result.md directly from the task directory"
            # If result.md doesn't exist, that's also not a silent empty —
            # the caller sees no "result" key and no error, which the Go
            # backend treats as "no final result" (falls back to transcript).
        return json_response(self, 200, out)

    def _get_events(self, task_id: str, since: int):
        if not TASK_ID_RE.fullmatch(task_id):
            return json_response(self, 400, {"error": "invalid task id"})
        if self._load_status(task_id) is None:
            return json_response(self, 404, {"error": "unknown task"})
        events = []
        ep = self._task_dir(task_id) / "events.jsonl"
        if ep.exists():
            seq = 0
            for line in ep.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                seq += 1
                if seq <= since:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events.append({
                    "seq": seq,
                    "type": str(ev.get("type", "text")),
                    "content": str(ev.get("content", "")),
                    "tool": str(ev.get("tool", "")),
                })
        return json_response(self, 200, {"task_id": task_id, "events": events})

    def _cancel(self, task_id: str):
        if not TASK_ID_RE.fullmatch(task_id):
            return json_response(self, 400, {"error": "invalid task id"})
        status = self._load_status(task_id)
        if status is None:
            return json_response(self, 404, {"error": "unknown task"})
        if status.get("status") in ("queued", "running"):
            status["status"] = "cancelled"
            status["finished_at"] = utcnow()
            status["updated_at"] = status["finished_at"]
            atomic_write(self._task_dir(task_id) / "status.json",
                         json.dumps(status, ensure_ascii=False, indent=2))
            qf = QUEUE_DIR / f"{task_id}.json"
            if qf.exists():
                qf.unlink()  # never picked up by the hook
            self.log_message("cancelled task %s", task_id)
        return json_response(self, 200, {"ok": True})



# B5: Timeout semantics (explicit contract):
#
# - timeout_s = max wall-clock seconds for the entire task, measured from
#   `started_at` (when the worker marks it running). Not from creation,
#   not from last update.
# - Queued tasks: if not claimed (moved from queue/ to tasks/) within
#   timeout_s of creation, they expire and are marked failed.
# - Running tasks: if now - started_at > timeout_s, the worker is presumed
#   lost. Marked failed. This does NOT mean the worker's side effects
#   stopped — only that we no longer expect a result.
# - timeout_s = 0 or null means "no deadline" (default 1 hour if unset).
#
# Reclaim runs in a background thread every 30s, not just on status read,
# so orphaned tasks are cleaned up even if nobody polls.

RECLAIM_INTERVAL_S = 30
DEFAULT_TIMEOUT_S = 3600

def reclaim_orphaned_tasks():
    """Background thread: expire queued tasks and reclaim lost workers."""
    import time
    while True:
        try:
            time.sleep(RECLAIM_INTERVAL_S)
            now = datetime.now(timezone.utc)
            # Check queued tasks (not yet claimed)
            if QUEUE_DIR.exists():
                for qf in QUEUE_DIR.glob("*.json"):
                    try:
                        data = json.loads(qf.read_text(encoding="utf-8"))
                        created = data.get("created_at", "")
                        timeout_s = data.get("timeout_s") or DEFAULT_TIMEOUT_S
                        if timeout_s <= 0:
                            timeout_s = DEFAULT_TIMEOUT_S
                        try:
                            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            age_s = (now - created_dt).total_seconds()
                            if age_s > timeout_s:
                                # Expired in queue — remove it so watcher doesn't pick it up
                                task_id = qf.stem
                                qf.unlink()
                                # Record the expiry in tasks dir for visibility
                                task_dir = TASKS_DIR / task_id
                                task_dir.mkdir(parents=True, exist_ok=True)
                                status = {
                                    "task_id": task_id,
                                    "status": "failed",
                                    "error": f"expired in queue after {int(age_s)}s (timeout {timeout_s}s)",
                                    "created_at": created,
                                    "finished_at": utcnow(),
                                    "updated_at": utcnow(),
                                }
                                atomic_write(task_dir / "status.json",
                                           json.dumps(status, ensure_ascii=False, indent=2))
                        except (ValueError, TypeError):
                            pass
                    except (OSError, ValueError):
                        pass
            # Check running tasks (claimed but worker lost)
            if TASKS_DIR.exists():
                for task_dir in TASKS_DIR.iterdir():
                    if not task_dir.is_dir():
                        continue
                    status_path = task_dir / "status.json"
                    if not status_path.exists():
                        continue
                    try:
                        status = json.loads(status_path.read_text(encoding="utf-8"))
                        if status.get("status") != "running":
                            continue
                        started = status.get("started_at") or status.get("updated_at", "")
                        # timeout_s was saved in status at claim time; fall back to default
                        timeout_s = status.get("timeout_s") or DEFAULT_TIMEOUT_S
                        if timeout_s <= 0:
                            timeout_s = DEFAULT_TIMEOUT_S
                        try:
                            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                            age_s = (now - started_dt).total_seconds()
                            if age_s > timeout_s:
                                status["status"] = "failed"
                                status["error"] = (
                                    f"worker lost: running for {int(age_s)}s "
                                    f"exceeds timeout {timeout_s}s "
                                    f"(from started_at; side effects may still be running)"
                                )
                                status["finished_at"] = utcnow()
                                status["updated_at"] = status["finished_at"]
                                atomic_write(status_path,
                                           json.dumps(status, ensure_ascii=False, indent=2))
                        except (ValueError, TypeError):
                            pass
                    except (OSError, ValueError):
                        pass
        except Exception:
            # Never let the reclaim thread die silently
            pass


def main() -> None:
    token = os.environ.get("MUSE_RECEPTIONIST_TOKEN", "")
    if not token:
        sys.stderr.write(
            "ERROR: MUSE_RECEPTIONIST_TOKEN is not set. "
            "Generate one (e.g. `openssl rand -hex 32`) and export it.\n"
        )
        sys.exit(1)
    host = os.environ.get("MUSE_RECEPTIONIST_HOST", "127.0.0.1")
    port = int(os.environ.get("MUSE_RECEPTIONIST_PORT", "8765"))
    # N2: Task directories contain sensitive tokens; owner-only.
    QUEUE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    TASKS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Ensure existing dirs are also locked down (in case they were
    # created before this fix).
    try:
        os.chmod(QUEUE_DIR, 0o700)
        os.chmod(TASKS_DIR, 0o700)
    except OSError:
        pass

    server = ThreadingHTTPServer((host, port), Receptionist)
    server.expected_token = token
    server.daemon_threads = True
    sys.stderr.write(
        f"multica-muse-receptionist {VERSION} listening on {host}:{port} "
        f"(queue={QUEUE_DIR})\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
