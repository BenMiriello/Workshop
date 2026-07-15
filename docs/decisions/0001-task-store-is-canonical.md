# 0001. The Task store is the canonical ticket substrate

- Status: accepted
- Date: 2026-07-15

## Context

The operator asked for one work concept ("tickets") instead of the split between
Tasks, Jobs, and scattered to-do lists. The shipped substrate already models this:
per-scope `.tasks.json` stores drive `services/tasks.py`, with `Task.job_ids` and
`Job.task_id` linking a ticket one-to-many to its runs. `Task.text` is
multi-line-lossless (first line is the title, continuation lines are the body) and
reconcile, export, and dedup all key on it. A proposal to add separate `title` and
`description` fields to the schema was pressure-tested and rejected: it would create
two sources of truth for the same string and force every reconcile, export, dedup,
and CLI path to agree on a join rule, all cost and no capability. See
`docs/platform-redesign/chat-tickets-orchestrator-plan-2026-07-15.md` (verdicts D1,
D2, D4).

## Decision

The Task store (`services/tasks.py`, per-scope `.tasks.json`) is the one canonical
record for work items. Task and Job stay separate records: the Task is the primary
"ticket" surface and its Jobs render inside it as the run log. There is one write
path to a task's text through `tasks.update(text=...)`; the first line is the title
and the remainder is the description, by convention, with no schema change. The
store, CLI, docs, and API keep the noun `task`; UI copy may say "ticket" but
identifiers stay `task`. A fresh third noun ("ticket", "issue") in code is drift and
is disallowed.

## Consequences

The attempts model is preserved: one ticket, N runs. Title and description become a
frontend presentation convention in one shared module, not stored fields. No
migration is needed. A pre-existing wart is accepted, not fixed now: two tasks with
identical first lines collide in reconcile's `by_text` map (first wins), made rare
by dedup. GitHub Issues sync stays out of scope; the Task store is the source of
truth, not a mirror of an external tracker.
