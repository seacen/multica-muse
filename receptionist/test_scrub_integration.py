#!/usr/bin/env python3
"""Integration tests: server.py actually calls scrub_task_token.

Claude Code round5: unit tests on the helper don't prove the server
calls it. These tests exercise the real HTTP paths:
1. Claimed task cancelled -> request.json has no task_token
2. Worker lost (timeout) -> request.json has no task_token

Run: python3 test_scrub_integration.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).parent
TOKEN = "test-token-scrub-123"
PORT = 18766


def api(method, path, body=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode()), e.code


def main():
    tmpdir = Path(tempfile.mkdtemp(prefix="scrub-it-"))
    queue_dir = tmpdir / "queue"
    tasks_dir = tmpdir / "tasks"
    queue_dir.mkdir()
    tasks_dir.mkdir()

    env = os.environ.copy()
    env["MUSE_RECEPTIONIST_TOKEN"] = TOKEN
    env["MUSE_RECEPTIONIST_PORT"] = str(PORT)
    env["MUSE_QUEUE_DIR"] = str(queue_dir)
    env["MUSE_TASKS_DIR"] = str(tasks_dir)
    # Speed up reclaim for the worker-lost test
    env["MUSE_RECLAIM_INTERVAL_S"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(REPO / "server.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    try:
        # --- Test 1: claimed task cancelled ---
        task, code = api("POST", "/v1/execute", {
            "prompt": "scrub test 1",
            "protocol_version": 1,
            "task_token": "mat_scrub_test_1",
        })
        assert code == 200, f"execute: {task}"
        tid = task["task_id"]

        # Simulate worker claim: mv queue file to tasks/<id>/request.json
        task_dir = tasks_dir / tid
        task_dir.mkdir(exist_ok=True)
        shutil.move(str(queue_dir / f"{tid}.json"),
                    str(task_dir / "request.json"))
        # Mark running via helper (like a real worker)
        from task_state import transition
        sys.path.insert(0, str(REPO))
        # Need status.json first
        (task_dir / "status.json").write_text(json.dumps({
            "task_id": tid, "status": "queued"}))
        assert transition(tasks_dir, tid, "running") == 0

        # Verify token is there before cancel
        req = json.loads((task_dir / "request.json").read_text())
        assert req.get("task_token") == "mat_scrub_test_1"

        # Cancel via HTTP
        c, code = api("POST", f"/v1/tasks/{tid}/cancel")
        assert code == 200, f"cancel: {c}"

        # Verify token scrubbed
        req = json.loads((task_dir / "request.json").read_text())
        assert "task_token" not in req, \
            f"FAIL: task_token not scrubbed on cancel: {req.keys()}"
        print("PASS: cancel scrubs task_token")

        # --- Test 2: worker lost ---
        task, code = api("POST", "/v1/execute", {
            "prompt": "scrub test 2",
            "protocol_version": 1,
            "timeout_s": 2,  # 2 second timeout
            "task_token": "mat_scrub_test_2",
        })
        assert code == 200
        tid2 = task["task_id"]
        task_dir2 = tasks_dir / tid2
        task_dir2.mkdir(exist_ok=True)
        shutil.move(str(queue_dir / f"{tid2}.json"),
                    str(task_dir2 / "request.json"))
        (task_dir2 / "status.json").write_text(json.dumps({
            "task_id": tid2, "status": "queued"}))
        assert transition(tasks_dir, tid2, "running") == 0
        # Backdate started_at so reclaim sees it as lost
        st = json.loads((task_dir2 / "status.json").read_text())
        st["started_at"] = "2020-01-01T00:00:00Z"
        (task_dir2 / "status.json").write_text(json.dumps(st))

        # Wait for reclaim thread (1s interval)
        time.sleep(3)

        st, code = api("GET", f"/v1/tasks/{tid2}")
        assert st["status"] == "failed", f"expected failed, got {st}"
        assert "worker lost" in st.get("error", ""), f"error: {st}"

        req = json.loads((task_dir2 / "request.json").read_text())
        assert "task_token" not in req, \
            f"FAIL: task_token not scrubbed on worker lost"
        print("PASS: worker lost scrubs task_token")

        print("\nAll integration tests passed!")

    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    # server.py needs to support MUSE_RECLAIM_INTERVAL_S
    main()
