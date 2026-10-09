# multica-muse

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.x](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![Works with Multica](https://img.shields.io/badge/works%20with-Multica-orange)](https://github.com/multica-ai/multica)
![Delegates to Muse](https://img.shields.io/badge/delegates%20to-Muse-7c3aed)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/seacen/multica-muse/pulls)

**[中文文档](README.zh-CN.md)**

**Give your Multica agents a personal AI.**

Multica agents run coding CLIs. Muse is not a CLI — it is your personal AI,
with your context, your tools, and your memory. multica-muse bridges the two
over HTTP, so your Multica agents can delegate tasks to it.

## Why

- Multica agents are strong, but they only run the CLIs you give them.
- Your Muse app already does more: shell, browser, files, memory, and judgment across tools.
- multica-muse connects the two. Setup takes minutes.

## How it works

```
Multica server
    │  dispatches a task
    ▼
multica daemon  (muse backend, Go)
    │  POST /v1/execute
    ▼
receptionist  (Python, your Muse cloud computer, 127.0.0.1:8765)
    │  queues the task
    ▼
queue watcher  (event hook, checks every 5s)
    │  wakes a worker
    ▼
worker  (your Muse app)
    │  runs it with full capabilities
    ▼
result travels back up the chain → appears in Multica
```

The receptionist is one Python file. It uses the standard library only. No dependencies to install.

## Quickstart

**Option A — via Muse (recommended).**

> Tell your Muse: "Set up multica-muse from https://github.com/seacen/multica-muse."

Your Muse fetches the [skill](skills/multica-muse-setup/SKILL.md) and runs
the full setup: install the receptionist, log in the daemon, register the
queue watcher, run a smoke test, and create a dedicated side chat where all
task notifications land.

**Option B — manual.**

**1. Run the installer.**

```bash
git clone https://github.com/seacen/multica-muse.git
cd multica-muse
MULTICA_TOKEN=mul_your_token_here ./install.sh --server https://your-multica-server
```

Use `--saas` instead of `--server` for Multica's hosted service. Create the token in Multica first: avatar → Settings → API Tokens. Passing it as an environment variable keeps it out of your shell history.

**2. Ask your Muse to register the queue watcher and create the side chat.**

> "Register the multica-muse queue watcher hook, and create the dedicated side chat for task notifications."

The hook step needs agent tools. A shell script cannot do it. Your Muse runs a dry run first (empty queue, then a test task), then enables the hook.

**3. Dispatch a task from Multica.**

In Multica, create an agent on the `muse` runtime. Send it a task. The result comes back to your Multica chat.

## How a task flows

1. A task reaches a Multica agent on the `muse` runtime.
2. The daemon POSTs the prompt to your receptionist.
3. The receptionist queues it. The watcher wakes a Muse worker within seconds.
4. The worker runs the task with your full Muse capabilities.
5. The result travels back. You see it in Multica.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MUSE_RECEPTIONIST_TOKEN` | *(required)* | Bearer token. The daemon must send the same value. |
| `MUSE_RECEPTIONIST_HOST` | `127.0.0.1` | Listen address. Keep it on localhost. |
| `MUSE_RECEPTIONIST_PORT` | `8765` | Listen port. |

Health check: `curl http://127.0.0.1:8765/healthz` returns `{"ok":true}`.

The daemon reads `MUSE_ENDPOINT` and `MUSE_TOKEN` from `~/.config/multica-muse/daemon.env`. The installer writes this file for you.

## Troubleshooting

- **The hook never fires.** The hook needs `hooks.enable`, and only after the dry run passes both branches (empty queue → silent, test task → wake). Ask your Muse to check its status.
- **Login fails with "unknown flag".** Newer daemon builds use `multica login --token`, not `multica auth login --token`.
- **The daemon cannot reach the receptionist.** Check `MUSE_ENDPOINT` in `~/.config/multica-muse/daemon.env`. Host and port must match the receptionist.
- **No systemd on your Muse cloud computer.** Start the receptionist in the background with `./start.sh` instead of the service file.

## Contributing

PRs are welcome. Open an issue first if the change is large.

This repo is the installer and the on-machine side. The companion `muse` backend for the Multica server lives in the [Multica repo](https://github.com/multica-ai/multica/pull/9155).

## License

MIT. See [LICENSE](LICENSE).
