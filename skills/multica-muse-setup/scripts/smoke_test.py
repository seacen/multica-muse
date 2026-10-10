#!/usr/bin/env python3
"""Smoke-test the Multica->Muse chain.

POSTs a trivial task to the receptionist, polls until it reaches a terminal
status, and prints the result. Exit 0 on completed, 1 otherwise.

Reads the bearer token from ~/.config/multica-muse/daemon.env (MUSE_TOKEN=)
or ~/.config/multica-muse/receptionist.env (MUSE_RECEPTIONIST_TOKEN=).
Stdlib only.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("MUSE_ENDPOINT", "http://127.0.0.1:8765")
TIMEOUT_S = 15
POLL_S = 5
DEADLINE_S = 600


def bearer_token():
    homes = [
        ("~/.config/multica-muse/daemon.env", "MUSE_TOKEN="),
        ("~/.config/multica-muse/receptionist.env", "MUSE_RECEPTIONIST_TOKEN="),
    ]
    for path, prefix in homes:
        try:
            with open(os.path.expanduser(path)) as f:
                for line in f:
                    if line.startswith(prefix):
                        return line.strip().split("=", 1)[1]
        except OSError:
            continue
    sys.exit("no bearer token found in ~/.config/multica-muse/{daemon,receptionist}.env")


TOKEN = bearer_token()


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        ENDPOINT + path,
        method=method,
        data=data,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {method} {path}: "
                 f"{e.read().decode()[:200]}")


def main():
    health = api("GET", "/healthz")
    assert health.get("ok"), f"receptionist unhealthy: {health}"
    print(f"receptionist ok ({ENDPOINT})", flush=True)

    task = api("POST", "/v1/execute", {
        "prompt": "Smoke test: reply with exactly this sentence: smoke test ok. "
                  "Do nothing else.",
        "timeout_s": 300,
    })
    tid = task["task_id"]
    print(f"queued {tid} — waiting for the hook worker…", flush=True)

    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        st = api("GET", f"/v1/tasks/{tid}")
        status = st.get("status")
        if status in ("completed", "failed", "cancelled"):
            print(f"terminal status: {status}")
            result = st.get("result", "")
            if result:
                print("--- result ---")
                print(result[:2000])
            if st.get("error"):
                print("error:", st["error"])
            # N4: Don't just check status — verify the worker actually
            # produced the expected result. A "completed" with no result
            # (or wrong result) is a failure, not a pass.
            if status != "completed":
                sys.exit(f"smoke test failed: terminal status was {status}")
            if "smoke test ok" not in result:
                sys.exit(f"smoke test failed: expected 'smoke test ok' in result, got: {result[:200]!r}")
            print("smoke test PASSED: worker returned expected result")
            sys.exit(0)
        time.sleep(POLL_S)
    # N4: Try to cancel the orphaned task before giving up
    try:
        api("POST", f"/v1/tasks/{tid}/cancel", {})
        print(f"cancelled orphaned smoke task {tid}", flush=True)
    except SystemExit:
        pass
    sys.exit(f"timed out after {DEADLINE_S}s waiting for {tid}")


if __name__ == "__main__":
    main()
