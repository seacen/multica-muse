# Worker instruction: execute one Multica-delegated task

You were woken because a Multica agent delegated a task to Muse via the
receptionist queue. The wake payload contains `{"task_id": "<id>"}`.
Execute the task below. **This execution must never be silent**: every task
gets its own visible side chat in the user's Muse app (start message,
progress, finish summary). The user explicitly asked for this.

## 1. Claim the task (atomic)

- Read `~/workspace/multica-muse/queue/<task_id>.json` → fields:
  `prompt` (the actual task), `session_id` (may be null), `timeout_s` (may be null).
- Claim it with an atomic rename:
  `mv ~/workspace/multica-muse/queue/<task_id>.json ~/workspace/multica-muse/tasks/<task_id>/request.json`
- If the rename fails (file already gone): another worker claimed it.
  End quietly with `muse.nothing_to_do`.

## 2. Mark running

Update `~/workspace/multica-muse/tasks/<task_id>/status.json`:
set `status` to `"running"` and `updated_at` to the current UTC time
(keep the other fields). Write it atomically (write temp file, then rename).

## 3. Side chat（当前由主代理事后创建，worker 跳过）

实测结论（2026-10-09）：hook worker 没有 `chat.create`/`chat.send_message`
能力（`chat.*` 是主会话专属）。所以 worker **不要尝试建会话**，
只保证任务目录完整；主代理收到 worker 的完成 handoff 后，
会为每个任务创建独立 side chat 并发布结果归档。

worker 在此步只做一件事：把 `started_at`（UTC ISO8601）写入
`~/workspace/multica-muse/tasks/<task_id>/notify.json`
（`{"task_id": ..., "started_at": ...}`，没有 chat_id）。
如果将来某天 `chat.create` 可用了，再恢复下面的原始流程。

<details><summary>原始流程（worker 有 chat 能力时恢复）</summary>

1. `chat.create` with `context_mode="fresh"` and
   `name="🛰️ Multica任务 <task_id前8位>: <prompt前40字>"`.
2. Save the returned `chat_id` into `notify.json`.
3. `chat.send_message` to that `chat_id` with an instruction for the
   chat's agent to publish the visible start message (in Simplified Chinese).
4. One-liner into the main chat so the user notices.

</details>

## 4. Execute

Run the prompt with your full capabilities (shell, browser, files, tools),
using the `workdir` from `request.json` as your working directory.

### Writing back to Multica (comments, issue reads)

You receive a task-scoped API credential for this: `request.json` contains
`task_token` (a `mat_...` token minted by the Multica server for this task).
Export it and use the `multica` CLI directly — this passes the CLI's
daemon-context check, same as CLI backends (Hermes/Codex) get:

```bash
export MULTICA_TOKEN=$(jq -r '.task_token' ~/workspace/multica-muse/tasks/<task_id>/request.json)
~/workspace/multica-muse/bin/multica --profile muse issue get <issue-id> --output json
```

To post a comment via REST (note: `workspace_id` goes in the query string, not the body):
```bash
export MULTICA_TOKEN=$(jq -r '.task_token' ~/workspace/multica-muse/tasks/<task_id>/request.json)
SERVER=$(jq -r '.server_url' ~/workspace/multica-muse/tasks/<task_id>/request.json)
WS=$(jq -r '.workspace_id' ~/workspace/multica-muse/tasks/<task_id>/request.json)
curl -s -X POST "$SERVER/api/issues/<issue-id>/comments?workspace_id=$WS" \
  -H "Authorization: Bearer $MULTICA_TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"你的评论内容"}'
```
(Get the server URL from `~/workspace/multica-muse/bin/multica --profile muse config get server_url`, or ask the user.)

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
- For long tasks, post at most 2–3 milestone updates to the side chat via
  `chat.send_message` (instruction to publish a short progress line).
  Do not spam: start + milestones + finish is enough.

## 5. Finish: result + status

- Write `~/workspace/multica-muse/tasks/<task_id>/result.md`: a concise
  Markdown summary of what was done and the outcome (this is what the Go
  backend returns to the Multica agent).
- Update `status.json`: `status` → `"completed"` (or `"failed"` with an
  `error` string, or `"cancelled"`), plus `finished_at` (UTC ISO8601),
  `result_preview` (first 300 chars of the result), `updated_at`.

## 6. Finish handoff（主代理据此建归档会话）

`chat.send_message` worker 不可用，此步改为：确保任务目录完整，
并让最终的 execute summary（即 handoff 内容）包含以下字段，
主代理收到后会创建独立 side chat 并发布归档：

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
  failure to the side chat.
- The user can ask follow-up questions inside the task's side chat; its
  agent answers from the task directory files.
