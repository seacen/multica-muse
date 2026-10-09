---
name: "multica-muse-setup"
description: "Set up the Multica→Muse integration and its ongoing operation: install the receptionist, log in a muse-backend daemon, register the queue-watcher hook, verify end-to-end, and create the dedicated side chat that receives all task notifications. Use when the user wants their Multica agents to delegate work to their Muse app."
compatibility: "Requires Muse (Meta's personal AI agent) on Linux with hooks and chat APIs. Not for generic Claude Code."
---

# Multica→Muse Setup

## Purpose
Set up the full chain so a user's Multica agents can delegate tasks to their
Muse app: Python receptionist → muse-backend daemon → queue-watcher hook →
verified end-to-end.

## Prerequisites
- The user's Multica server URL (selfhost, e.g. `https://multica.example.com`;
  SaaS uses `--saas`).
- A Multica personal access token (`mul_...`), created in the Multica web UI
  (avatar → Settings → API Tokens). **Ask the user to paste it in chat at
  runtime. Never write it into any file** — the daemon persists it in its own
  profile config via the login command. Never echo it in outputs.

## Workflow

### 1. Install receptionist + daemon
Use the public repo (source of truth for the installer):

```bash
git clone https://github.com/seacen/multica-muse /tmp/multica-muse
cd /tmp/multica-muse
MULTICA_TOKEN='<user-pasted-token>' ./install.sh --server <url>   # or --saas
```

This does 5 things:
- Generates the receptionist bearer token and starts the receptionist on `127.0.0.1:8765`.
- Downloads the muse-backend daemon binary.
- Logs in under a dedicated `muse` profile (does not touch existing profiles).
- Writes `MUSE_ENDPOINT`/`MUSE_TOKEN` to `~/.config/multica-muse/daemon.env`.
- Starts the daemon.

If login fails, see `references/troubleshooting.md` — newer daemon builds moved `--token` from `auth login` to `multica login`.

### 2. Register the queue-watcher hook
Shell cannot do this — use the hooks tools:

1. Create the scripts directory (it does not exist on a fresh Muse VM) and copy the detector script:
   ```bash
   mkdir -p ~/hooks/scripts
   cp /tmp/multica-muse/hook/queue-watcher.sh ~/hooks/scripts/multica-muse-queue.sh
   chmod +x ~/hooks/scripts/multica-muse-queue.sh
   ```
2. `hooks.add` with:
   - `id`: `multica-muse-queue`
   - `script_path`: `~/hooks/scripts/multica-muse-queue.sh`
   - `prompt`: the full contents of `/tmp/multica-muse/hook/worker-prompt.md`
   - `poll_interval_secs`: `5` (the minimum the hooks API allows)
3. `hooks.dry_run` — must report `silent` on an empty queue. Then drop a
   synthetic file `~/workspace/multica-muse/queue/mt-dryrun.json`
   (`{"prompt":"dry run, ignore"}`), dry-run again (must report `wake`),
   and delete the file.
4. `hooks.enable` — only after both dry-run branches pass.

### 3. Smoke-test end-to-end
Run `scripts/smoke_test.py` from the skill directory:
```bash
python3 scripts/smoke_test.py
```
It reads the bearer token from `~/.config/multica-muse/daemon.env`, POSTs a
smoke task to the receptionist, polls until terminal, and prints the result.
Expect `completed`. This proves queue → hook → worker → result retrieval.

### 4. Create the dedicated side chat
All Multica work and notifications live in one side chat — never the main chat, never per-task side chats (hook workers have no `chat.*` capability, so per-task live chats are impossible anyway):
- `chat.create` with `context_mode: "fork"` and a name like `🛰️ Multica 联动`.
- Record its `chat_id`: every task-completion notification goes here from now on.
- Tell the user: this chat is the primary interface. They talk to the agent here; the agent syncs to Multica (see `references/chat-sync.md`).

### 5. Hand off to the user
Tell the user: in the Multica web UI, create an agent on the `muse` runtime and dispatch a task. The result will surface in the dedicated side chat.

## Output Contract
- `curl -fsS http://127.0.0.1:8765/healthz` → `{"ok":true,...}`.
- `multica daemon status --profile muse` shows running, with the `muse`
  runtime registered (check the daemon log for
  `registered runtime ... provider=muse`).
- Hook `multica-muse-queue` is enabled at a 5s interval.
- `scripts/smoke_test.py` exits 0 with a `completed` task and a retrieved result.

## Auth
- The `mul_` token is supplied by the user at runtime via chat paste. Use it
  transiently for the login step only. Do not store it in skill files, logs,
  memory, or shell history (prefer the `MULTICA_TOKEN=...` env form).
- The receptionist bearer token lives in
  `~/.config/multica-muse/receptionist.env` (mode 600). Read it only to wire
  the daemon or run the smoke test; never print its value.

## Operating Rules
1. Do not invent binaries, paths, or flags — the public repo's `install.sh`
   and `README.md` are the source of truth; check
   `references/troubleshooting.md` when reality deviates.
2. Never enable the hook before both dry-run branches pass.
3. Hook workers have no `chat.*` capability — do not promise live per-task side chats. All completion notifications go to the dedicated side chat from step 4.
4. Keep the user's token out of command lines where possible; never include
   it in any file you write or any tool output you show.
5. The daemon needs `MUSE_ENDPOINT`/`MUSE_TOKEN` in its process environment:
   `set -a; . ~/.config/multica-muse/daemon.env; set +a` before
   `multica daemon start --profile muse`.
