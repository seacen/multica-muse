# Upgrading an existing install

Use this when the daemon is already installed and a newer release is out
(`muse-backend-vN` on `seacen/multica`), or the user asks to upgrade.

An upgrade swaps the daemon binary and restarts it. The receptionist, the hook
and the side chat stay as they are.

## Two things to know first

- **Do not re-run `install.sh` for an upgrade.** It logs in again on every run,
  so it needs a fresh `mul_` token from the user. The daemon is already logged
  in under the `muse` profile and keeps that login across a binary swap.
- **Run the upgrade from the user's chat, not from a Multica task.** Step 5
  stops the daemon, and any task it is running stops with it, including a task
  that asked you to upgrade.

## Steps

### 1. Pick the release

```bash
cd /tmp/multica-muse 2>/dev/null && git pull || git clone https://github.com/seacen/multica-muse /tmp/multica-muse
grep -o 'DAEMON_RELEASE_TAG:-[^}]*' /tmp/multica-muse/install.sh
```

The tag after `:-` is the current release. Use the one the user names if they
name one.

### 2. Find the binary the daemon runs from

```bash
PID=$(cat ~/.multica/profiles/muse/daemon.pid 2>/dev/null || pgrep -f 'daemon start --foreground.*--profile muse' | head -1)
BIN=; [ -n "$PID" ] && [ -e "/proc/$PID/exe" ] && BIN=$(readlink -f "/proc/$PID/exe")
echo "${BIN:-not found}"
```

Replace that file in place. `install.sh` puts it at `~/.local/bin/multica`, but
an install may live elsewhere, and a new binary written next to the old one
changes nothing: the daemon and any autostart entry keep pointing at the old
path. If this prints `not found` (the daemon is not running), use
`command -v multica`, and ask the user if that is empty too.

The commands below use `$BIN`. If your shell does not keep variables between
commands, set `BIN=<the path above>` again first, the same for `$TAG` and `$C`.

### 3. Check that no task is in flight

```bash
ls ~/workspace/multica-muse/queue/
grep -lE '"status": "(queued|running)"' ~/workspace/multica-muse/tasks/*/status.json 2>/dev/null
```

Both should print nothing. If either prints a task, wait for it to finish and
check again.

### 4. Download and test the new binary

```bash
TAG=muse-backend-v4    # the tag from step 1
ARCH=$(uname -m); case "$ARCH" in x86_64) ARCH=amd64;; aarch64|arm64) ARCH=arm64;; esac
curl -fsSL --max-time 120 -o "$BIN.new" \
  "https://github.com/seacen/multica/releases/download/$TAG/multica-linux-$ARCH"
chmod +x "$BIN.new"
"$BIN.new" version
```

Stop if `version` fails. The running daemon has not been touched yet.

### 5. Swap, rewrite the env file, restart

```bash
C=~/.config/multica-muse
cp "$BIN" "$BIN.prev"
cp "$C/daemon.env" "$C/daemon.env.prev"

"$BIN" daemon stop --profile muse
mv "$BIN.new" "$BIN"

T=$(grep -o 'MUSE_RECEPTIONIST_TOKEN=.*' "$C/receptionist.env" | cut -d= -f2)
printf 'MULTICA_MUSEAPP_ENDPOINT=http://127.0.0.1:8765\nMULTICA_MUSEAPP_TOKEN=%s\n' "$T" > "$C/daemon.env"
chmod 600 "$C/daemon.env"
echo "seacen/multica@$TAG" > "$C/installed-daemon-release"; chmod 600 "$C/installed-daemon-release"

set -a; . "$C/daemon.env"; set +a
"$BIN" daemon start --profile muse
```

The env file is rewritten every time, the same way `install.sh` writes it.
Installs from before 2026-10-30 only have the old `MUSE_ENDPOINT`/`MUSE_TOKEN`
names. The current daemon reads only `MULTICA_MUSEAPP_*`, and without them it
never advertises the `museapp` runtime. Never print `$T`.

### 6. Verify

```bash
"$BIN" version
"$BIN" daemon status --profile muse
grep 'registered runtime' ~/.multica/profiles/muse/daemon.log | tail -3
python3 /tmp/multica-muse/skills/multica-muse-setup/scripts/smoke_test.py
```

- `version` shows the new build.
- `daemon status` lists `museapp` among the agents.
- The log has a fresh `registered runtime ... provider=museapp` line for each workspace.
- The smoke test exits 0.

If `daemon status` also lists `muse`, the daemon found a `muse` CLI on PATH.
That is Meta's Muse Code backend, a separate runtime, and it does not affect this
integration.

### 7. Tell the user

Report the new version string, and tell them that the Multica side has one step
left for a workspace admin. A runtime's identity includes its provider, so
moving from a `muse` release to a `museapp` release creates a new runtime in
Multica, and the old one goes offline. Agents bound to the old runtime have to
be switched to the new one, in the agent's settings or with
`multica agent update <agent-id> --runtime-id <new-runtime-id>`. Once nothing
uses the old runtime, it can be deleted. You cannot do this step from here: it
needs the workspace's Multica admin.

## Rolling back

```bash
C=~/.config/multica-muse
BIN=<the path from step 2>
"$BIN" daemon stop --profile muse
mv "$BIN.prev" "$BIN"
mv "$C/daemon.env.prev" "$C/daemon.env"
set -a; . "$C/daemon.env"; set +a
"$BIN" daemon start --profile muse
```

Restore the env file together with the binary: an older release may read the
old variable names.
