# Troubleshooting — Multica→Muse setup

Lessons from the first real trial (2026-10-09). Read when a step deviates.

## Daemon login: `auth login --token` vs `multica login --token`

`install.sh` runs `multica auth login --token`. On current daemon builds the
`--token` flag moved to the top-level command: `auth login --token` fails
with `unknown flag: --token`. Fix — run the login manually, then continue
install.sh from step 4 (write `daemon.env`, start daemon):

```bash
MULTICA_SERVER_URL=<url> multica login --token '<mul_...>' --profile muse
```

Verify with `multica auth status --profile muse`.

## First login times out

The first `login` attempt can time out even when the server is healthy
(transient). Retry once; raise the limit with `MULTICA_HTTP_TIMEOUT=60` if
needed. Confirm reachability first:
`curl -s -o /dev/null -w "%{http_code}" https://<server>/` should return 200.

## No systemd user manager

`install.sh` tries `systemctl --user`; when unavailable it falls back to a
background process (`nohup start.sh`). Verify with
`curl -fsS http://127.0.0.1:8765/healthz`. The service will not survive a VM
reboot in this mode — re-run `./start.sh` after restarts.

## Daemon doesn't see the muse runtime

1. Confirm the env reached the daemon process: it must have been started
   with `MUSE_ENDPOINT`/`MUSE_TOKEN` in its environment
   (`set -a; . ~/.config/multica-muse/daemon.env; set +a` first).
2. `multica daemon status --profile muse` should list `muse` among agents.
3. Grep the daemon log
   (`~/.multica/profiles/muse/daemon.log`) for
   `registered runtime ... provider=muse` — one line per workspace.
   If the receptionist was down at daemon start, the daemon re-probes on its
   converge loop, so it recovers by itself after a few rounds.

## Hook never fires

- `hooks.list` — is `multica-muse-queue` enabled?
- `hooks.logs --id multica-muse-queue` — check recent decisions.
- The detector watches `$HOME/workspace/multica-muse/queue/*.json`. If the
  repo was cloned elsewhere, make sure `QUEUE` in the installed
  `~/hooks/scripts/multica-muse-queue.sh` points at the live queue dir.
- Minimum poll interval is 5s — 1–2s is not supported by the hooks API.

## Smoke task stays queued

The worker claims tasks with an atomic `mv` of
`queue/<id>.json` → `tasks/<id>/request.json`. If the file never moves, the
hook isn't waking a worker (see above). If it moves but nothing happens, read
`tasks/<id>/status.json` and `events.jsonl` for the worker's trail.

## Token hygiene

- The `mul_` token is long-lived: the daemon stores it in its profile config
  and uses it for all API calls. It cannot be revoked after setup without
  breaking the daemon — rotate by re-running the login step.
- The receptionist bearer token (`~/.config/multica-muse/receptionist.env`,
  mode 600) is only shared between receptionist and daemon on the same host.
  If it leaks, regenerate it and rewrite `daemon.env`, then restart both.
- Never paste either token into chat logs, files, or tool outputs beyond the
  single login invocation.
