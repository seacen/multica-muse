# Multica Chat Sync

## Pattern: one primary interface

The dedicated side chat is the primary interface. Multica's web chat is
secondary. The user talks to the agent in the side chat; the agent mirrors
to Multica. Do not maintain two live interfaces — Multica has no outgoing
chat webhook, so Multica→side-chat auto-sync would need polling plus a relay,
adding latency and a failure mode for no real benefit.

## Side chat → Multica

When the user discusses Multica work in the side chat, POST the relevant
content to the Multica chat session so the Multica-side history stays
complete:

```bash
# Read the user's mul_ token from the daemon profile (never print it)
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.multica/profiles/muse/config.json'))['token'])")
curl -fsS -X POST "https://<server>/api/chat/sessions/<session_id>/messages?workspace_slug=<slug>" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "<message>"}'
```

Find the session via `GET /api/chat/sessions?workspace_slug=<slug>` and match
on the agent. The session id and workspace slug are also in each task's
`<workdir>/.multica/daemon_task_context.json`.

## Multica → side chat

Not automated. If the user (or someone else) writes in the Multica web UI,
the agent sees it on its next check:

```bash
curl -fsS "https://<server>/api/chat/sessions/<session_id>/messages?workspace_slug=<slug>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | tail -20
```

Summarize anything new into the side chat when the user asks, or when a
task notification references it. Do not poll on a schedule for this.

## Task completion notifications

Every completed Multica task posts one notification to the dedicated side
chat (never the main chat, never a new per-task side chat):

- One line: what finished, key result.
- Deep link, in priority order:
  1. Issue: `https://<server>/<workspaceSlug>/issues/<identifier>`
  2. Chat session: `https://<server>/<workspaceSlug>/chat?session=<chat_session_id>`
  3. Agent (only if the agent still exists): `https://<server>/<workspaceSlug>/agents/<agent_id>`
  4. If only the agents list page is available, post no link.
- IDs come from `<workdir>/.multica/daemon_task_context.json`
  (`agent_id`, `issue_id`, `chat_session_id`); the workspace slug from the
  workdir path.
