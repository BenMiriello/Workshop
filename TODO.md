# TODO

## Agents & Chat overhaul (operator review 2026-06-22)
Phase 1 — Agents tab + chat-UI bugs — DONE (commit 40dfd4a, reviewer-approved):
[x] Autonomy: fixed dead `/api/autonomy` → real on/off toggle on the cron flag.
[x] Agent cards: one hue per KIND (assistant vs build), not per-id hashing.
[x] Rename "Data" → "Assistant" everywhere user-facing (UI labels, tags, copy).
[x] Agent cards: uniform height, clamp wrapping (no ragged tiles).
[x] Agent cards: sort most-recent first; activity timestamp (<24h time, else date).
[x] "New builder" from Agents tab synced with the in-project spawn (editable name/slug/purpose).
[x] Focused chat `/chat/:key`: slimmed topnav + dropped SectionNav (toolbar height fix, web + mobile).
[x] Build-agent chat: consolidated the two expand/contract buttons into one (`Chat hideExpand`).
[x] Build-agent view: chat is the default focused view (per-agent persisted); chat always bounded + internally scrolling (never full-length, incl. mobile); region focus toggle visible on mobile. (reviewer-approved)
[x] Agent cards: kind tag no longer clipped ("ass…") on long titles — title absorbs flex shrink + ellipsizes, tag is flex:0 0 auto. (reviewer-approved)

Phase 2 — chat features:
[x] Chat timeout handling: clear "stopped at the limit — files saved, continue" message + calm warn render; was opaque "(model exited without text)". (reviewer-approved)
[x] Chat stream-limit fix: bumped the build-agent stdout StreamReader limit to 64 MiB; a large stream-json event no longer aborts the turn with "Separator is found, but chunk is longer than limit". (reviewer-approved)
[x] Overlap handling: flock single-flight + near-duplicate dedupe (topic-signature containment) + vacuous-task filter on the Letta TODO writers, so reworded overseer spam is rejected at the source; new tasks land before the Log. (reviewer-approved)
[x] Chat slash commands: registry + "/" autocomplete popover + slash button (/new, /delete, /tasks, /expand, /help). /compact + /rollback land with their backends. (reviewer-approved)
[ ] Per-chat to-do/done list surfaced from the task store and prompted into the agent (Claude task-list bridge).
[~] DROPPED: build a chat `/compact`. Build agents run `claude -p --resume`, and Claude Code auto-compacts the session context itself — building our own is redundant. (The UI chat thread is a display log, not the model's context.) An optional read-only context-size indicator could be a future nicety; no compaction engine needed.
[ ] Conversation rollback/undo: message ids + truncate endpoint + "revert to here" UI → wires `/rollback`.
[ ] Resilience: graceful recovery when a build-agent turn is interrupted/derailed mid-task — checkpoint in-progress work + resume from PROJECT.md/notes instead of ending flat. (operator 2026-06-22)

## Overseer backlog (auto-generated)
[ ] Onboard new banks for Plaid MCP as per project_plaid_mcp.md.
[ ] Review recent notify lines for actionable items or anomalies.
[ ] Review and update system documentation for clarity and completeness.
[ ] Review and finalize the draft documentation for the latest feature release.
[ ] Review and finalize feedback for tenant full separation.
[ ] Review and finalize the draft for the upcoming project presentation. — proposed by overseer (normal)
[ ] Refactor the logging module to reduce verbosity and improve performance. — proposed by overseer (normal)

## Log
[2026-06-23T15:30Z] [overseer] Proposed task to review recent notify lines for anomalies.
[2026-06-23T11:52Z] [overseer] Proposed task to review and prioritize the top 3 open tasks.
[2026-06-23T07:46Z] [overseer] Proposed task to review recent notify lines for anomalies.
[2026-06-23T05:24Z] [overseer] Proposed task to review recent notify lines.
[2026-06-23T04:08Z] [overseer] Proposed task to review recent notifications for anomalies.
[2026-06-22T21:51Z] [overseer] Proposed task to review recent notify lines for anomalies.
