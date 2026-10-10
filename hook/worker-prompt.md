# Worker instruction: execute one Multica-delegated task

You were woken because a Multica agent delegated a task to Muse via the
receptionist queue. The wake payload contains `{"task_id": "<id>"}`.
Execute the task below. **This execution must never be silent**: report
progress and the finish summary to the user's dedicated Multica side chat
(the hook's delivery target). Do NOT create a new side chat per task.

## 1. Claim the task (atomic)

- Read `~/workspace/multica-muse/queue/<task_id>.json` → fields:
  `prompt` (the actual task), `session_id` (may be null), `timeout_s` (may be null).
- Claim it with an atomic rename:
  `mv ~/workspace/multica-muse/queue/<task_id>.json ~/workspace/multica-muse/tasks/<task_id>/request.json`
- If the rename fails (file already gone): another worker claimed it.
  End quietly with `muse.nothing_to_do`.

## 2. Mark running

Use the atomic state helper — do NOT write status.json directly:
```bash
python3 ~/.local/share/multica-muse/task_state.py \
  ~/workspace/multica-muse/tasks <task_id> running
```
If it exits non-zero with "invalid transition", the task was cancelled
(or already finished). Stop immediately with `muse.nothing_to_do`.

B4: This helper uses file locking for atomic read-modify-write. Terminal
states (cancelled/completed/failed) cannot be overwritten. Never bypass it
by writing status.json yourself.

## 3. Reporting (dedicated side chat, no per-task chats)

All Multica work is reported in the user's dedicated Multica side chat
(the hook's delivery target) — do NOT create a new side chat per task.

Worker 在此步只做一件事：把 `started_at`（UTC ISO8601）写入
`~/workspace/multica-muse/tasks/<task_id>/notify.json`
(`{"task_id": ..., "started_at": ...}`)。

主代理收到 worker 的完成 handoff 后，直接在专属 side chat 里发布结果。

## 4. Execute

Run the prompt with your full capabilities (shell, browser, files, tools),
using the `workdir` from `request.json` as your working directory.

### Writing back to Multica (comments, issue reads)

You receive a task-scoped API credential for this: `request.json` contains
`task_token` (a `mat_...` token minted by the Multica server for this task).
Export it and use the `multica` CLI directly — this passes the CLI's
daemon-context check, same as CLI backends (Hermes/Codex) get:

```bash
# Set all three once at the start. The CLI reads MULTICA_SERVER_URL before
# checking daemon-task context (upstream #6407), so this avoids the
# "No server configured" path without --server-url on every command.
REQ=~/workspace/multica-muse/tasks/<task_id>/request.json
export MULTICA_TOKEN=$(jq -r '.task_token' $REQ)
export MULTICA_SERVER_URL=$(jq -r '.server_url' $REQ)
export MULTICA_WORKSPACE_ID=$(jq -r '.workspace_id' $REQ)
~/.local/bin/multica issue get <issue-id> --output json
```

To post a comment (note: `workspace_slug` goes in the query string, not the body):
```bash
# Read server_url, workspace_id, and task_token from YOUR task's request.json.
# Do NOT hardcode a server URL or workspace.
REQ=~/workspace/multica-muse/tasks/<task_id>/request.json
export MULTICA_TOKEN=$(jq -r '.task_token' $REQ)
SERVER_URL=$(jq -r '.server_url' $REQ)
# Note: with task-token auth, the server binds the workspace from the token.
# Don't put workspace_id (a UUID) in workspace_slug — it's ignored anyway.
# Write the auth header to a file (0600) — don't put the token on the
# command line where `ps` can see it.
HDR=$(mktemp)
chmod 600 "$HDR"
printf 'Authorization: Bearer %s\n' "$MULTICA_TOKEN" > "$HDR"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$SERVER_URL/api/issues/<issue-id>/comments" \
  -H @"$HDR" -H "Content-Type: application/json" \
  -d '{"content":"你的评论内容"}'
rm -f "$HDR"
```

When your task says "reply with a comment" or "post the result to the
issue", do it via the CLI or curl above — do not leave the write-back
undone. The `result.md` you write is still the authoritative task result;
the comment is the user-visible delivery.
Never write any token into task files, logs, or events.

While working, append progress events to
`~/workspace/multica-muse/tasks/<task_id>/events.jsonl`, one JSON object
per line:

```json
{"type": "text", "content": "正在查 Multica 仓库的 daemon 协议…"}
{"type": "thinking", "content": "先看 messages.go 再看 claim 流程"}
{"type": "tool", "content": "browser.open https://github.com/…"}
```

- `type` is one of `text` (progress narration), `thinking` (brief reasoning
  note), `tool` (what tool you are about to call). Keep each `content`
  to one or two short lines; do not dump raw outputs.
- Sequence numbers are assigned by line order; just append.
- If `timeout_s` is set, stop new work when it elapses and report partial results.
- At reasonable checkpoints, re-read `status.json`: if `status` is
  `"cancelled"`, stop promptly and finish as cancelled (step 6).
- For long tasks, post at most 2–3 milestone updates by appending to events.jsonl
  (the main agent forwards them to the side chat).
  Do not spam: start + milestones + finish is enough.

## 5. Finish: result + status

- Write `~/workspace/multica-muse/tasks/<task_id>/result.md`: a concise
  Markdown summary of what was done and the outcome (this is what the Go
  backend returns to the Multica agent).
- Update status via the atomic helper (B4 — never write status.json directly):
```bash
python3 ~/.local/share/multica-muse/task_state.py \
  ~/workspace/multica-muse/tasks <task_id> completed
# or: ... <task_id> failed --error "what went wrong"
```
If it says "invalid transition", the task was cancelled while you worked.
Do NOT overwrite — stop with `muse.nothing_to_do`. Your result.md is kept
for the record, but the task stays cancelled.

## 6. Finish handoff（主代理据此建归档会话）

确保任务目录完整，并让最终的 execute summary（即 handoff 内容）包含以下字段，
主代理收到后直接在专属 side chat 里发布结果（不再为每个任务建独立 side chat）：

- `task_id`
- `status`（completed / failed / cancelled）
- 一句话中文摘要
- `result_preview`（result.md 前 300 字）
- `started_at` / `finished_at`（UTC ISO8601）

Update `notify.json` with `finished_at`, `status`, and `result_preview`.
handoff 模板：

```
🛰️ Multica 任务 <task_id> 已完成：<一句话中文摘要>
status=<completed|failed|cancelled> preview=<result_preview>
```

## Rules

- Never invent credentials or secrets; never write tokens into task files.
- Keep the task directory (`tasks/<task_id>/`) as the complete, openable log:
  `request.json`, `status.json`, `events.jsonl`, `result.md`, `notify.json`.
- If the prompt asks for something unsafe or impossible, mark the task
  `failed` with a clear `error` instead of improvising, and publish the
  failure to the dedicated Multica side chat.
- The user can ask follow-up questions in the dedicated Multica side chat; answers come from the task directory files.
