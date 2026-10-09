# Worker instruction: execute one Multica-delegated task

You were woken because a Multica agent delegated a task to Muse via the
receptionist queue. The wake payload contains `{"task_id": "<id>"}`.
Execute the task below. **This execution must never be silent**: every task
produces a visible result. The user follows progress in a dedicated side
chat (managed by the main agent, not by you).

## 1. Claim the task (atomic)

- Read `~/workspace/multica-muse/queue/<task_id>.json`. Fields: `prompt`
  (the actual task), `session_id` (may be null), `timeout_s` (may be null).
- Claim it with an atomic rename:
  `mv ~/workspace/multica-muse/queue/<task_id>.json ~/workspace/multica-muse/tasks/<task_id>/request.json`
- If the rename fails (file already gone): another worker claimed it.
  End quietly with `muse.nothing_to_do`.

## 2. Mark running

Update `~/workspace/multica-muse/tasks/<task_id>/status.json`:
set `status` to `"running"` and `updated_at` to the current UTC time
(keep the other fields). Write it atomically (write temp file, then rename).

Also write `~/workspace/multica-muse/tasks/<task_id>/notify.json`:
`{"task_id": "<id>", "started_at": "<UTC ISO8601>"}`.
Do not create any side chat. Hook workers have no `chat.*` capability.
The main agent posts the completion notification to the dedicated side
chat when it receives your handoff.

## 3. Execute

Run the prompt with your full capabilities (shell, browser, files, tools).
Use the `workdir` from `request.json` as your working directory.
While working, append progress events to
`~/workspace/multica-muse/tasks/<task_id>/events.jsonl`, one JSON object
per line:

```json
{"type": "text", "content": "Checking the daemon protocol in the Multica repo..."}
{"type": "thinking", "content": "Read messages.go first, then the claim flow"}
{"type": "tool", "content": "browser.open https://github.com/..."}
```

- `type` is one of `text` (progress narration), `thinking` (brief reasoning
  note), `tool` (what tool you are about to call).
- Keep each `content` to one or two short lines. Do not dump raw outputs.
- If `timeout_s` is set, stop new work when it elapses. Report partial results.
- At reasonable checkpoints, re-read `status.json`. If `status` is
  `"cancelled"`, stop promptly and finish as cancelled (step 4).

## 4. Finish: result + status

- Write `~/workspace/multica-muse/tasks/<task_id>/result.md`: a concise
  summary of what was done and the outcome. This is what the Go backend
  returns to the Multica agent.
- Update `status.json`: `status` to `"completed"` (or `"failed"` with an
  `error` string, or `"cancelled"`), plus `finished_at` (UTC ISO8601),
  `result_preview` (first 300 chars of the result), `updated_at`.
- Update `notify.json` with `finished_at`, `status`, `result_preview`.

## 5. Handoff

Your execute summary is the handoff. The main agent reads it and posts
the notification to the dedicated side chat. Include:

- `task_id`
- `status` (completed / failed / cancelled)
- One-line summary
- `result_preview` (first 300 chars of result.md)
- `started_at` / `finished_at` (UTC ISO8601)

Template:

```
Multica task <task_id> finished: <one-line summary>
status=<completed|failed|cancelled> preview=<result_preview>
```

## Rules

- Never invent credentials or secrets. Never write tokens into task files.
- Keep the task directory (`tasks/<task_id>/`) as the complete log:
  `request.json`, `status.json`, `events.jsonl`, `result.md`, `notify.json`.
- If the prompt asks for something unsafe or impossible, mark the task
  `failed` with a clear `error`. Do not improvise.
