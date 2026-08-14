# Tickets: one store, Postgres

Status: **decided, being implemented.** Written 2026-08-14.

This supersedes the mirror staging in
`data-model-and-persistence-2026-08-13.md` §P3. That plan had `.tasks.json`
stay authoritative with Postgres mirrored behind it, proven by a read-shadow
before a flip. The shadow did its job (it found two real data-loss bugs, below)
and the mirror is now deleted rather than promoted.

The operator settled the general question on the chat side: a database-backed
application depending on its database "is every web application that is based
off of a database, which is almost all of them." That is not a reason to keep
two stores. The same reasoning applies here, so the same conclusion does:

> Postgres is the ticket store. `.tasks.json` is retired. `TODO.md` survives as
> a one-way export.

## What the mirror was worth before it went

Not nothing, which is why it is recorded rather than quietly dropped. Running
the JSON store and the relational store side by side for a day surfaced two
faults that reading the code had not:

- `comment` had no `external_id`. `services/tasks.delete_comment` addresses a
  comment by exactly that id, so comments would have rendered after a cutover
  and been permanently undeletable.
- Comments were backfilled but neither mirrored on write nor returned on read,
  so any comment written after the backfill would have vanished at the flip.

Both are fixed. The lesson kept: a shadow is worth running, it is just not worth
keeping.

## The evidence for retiring `.tasks.json` and keeping `TODO.md`

Measured 2026-08-14 over both scopes (112 tickets).

**`TODO.md` is no longer a second write path, though it was one.** 22 commits in
history changed `TODO.md` without `.tasks.json`, but the last was 2026-07-18,
four weeks ago, and they cluster on a single day of an agent annotating lines by
hand. Today both scopes are byte-identical to their rendered export and
`TODO.md` shares an mtime with `.tasks.json` in both, meaning only `_persist`
has written either. The repo's own CLAUDE.md already forbids editing it
("a generated export, never edit it directly").

So `reconcile()` -- which heals out-of-band `TODO.md` edits back into the store,
and is called on *every* read path -- is legacy support for a workflow that
stopped a month ago and is now explicitly disallowed. Deleting it is what makes
the documented contract true instead of aspirational. `import_markdown` goes
with it.

**The one live out-of-band writer has no caller.**
`letta_tools/workshop.wkshp_propose_task` appends a line to `TODO.md` directly,
under its own `flock`, bypassing `services/tasks.py` entirely. It exists for the
`workshop-overseer` agent, which is not in the registry: only eight build agents
remain, `.runtime/agents` is empty, and there are no agent ticks. The tool is
still exposed at `/tools/wkshp_propose_task`, so it is reachable and must be
changed rather than assumed dead.

**`TODO.md` itself earns its place.** It is the human-readable and
agent-readable view, and it is injected into agent prompts. Keeping it costs one
render on write. Keeping it *readable back into the store* is what cost
correctness.

## Where every field goes

Occupancy measured across all 112 tickets. Nothing below is dropped on a guess.

**Already modelled:** `id` (as `external_id`, because every caller, every shell
script and every `TODO.md` row refers to tickets by the legacy uuid), `title`,
`body`, `status` (as a workflow state), `priority` (as the enum), `created`,
`updated`, `effort`, `execution` (as `ticket_type`), `depends_on` and
`relates_to` (as `ticket_link`), `parent`, `comments`. `text` stays a
projection of title+body and is never stored.

**Deleted, as duplicates of each other and of nothing:**

- `assignee` (112/112) and `owner` (112/112) are both the literal string
  `operator` on every ticket in both scopes. Two fields, one concept, zero
  information, and `ticket.assignee_id` already exists for when assignment
  becomes real. This is the ADR 0019 R4 rule applied to the platform's own
  invention.
- `attempts` (112/112) becomes `count(run)`, deleting a hand-maintained counter
  that had already produced a real bug.

**Given a column, because they are current state and get read on the hot path:**

- `source` (112/112, `{kind, ref}`) -> `source_kind` + `source_ref`. This is
  **the chat-to-ticket link**: `{kind: "chat", ref: <thread id>}` is how a
  ticket knows the conversation that produced it. It has been carried as an
  unindexed JSON blob and is the reason "show me this chat's tickets" is not a
  query today.
- `provenance` (56/112) is a sha256 spec hash -> `spec_hash`. It is what lets a
  queue triager terminate, so it is load-bearing despite looking opaque.
- `touches` (35/112) is a path list -> `touches text[]`.
- `blocked_reason` (23/112) -> a column. It is the answer to "why is this
  stuck", and a blocked ticket that has lost it is the sharp failure the
  backfill review already caught once.

**Moved to `run`, because they describe an execution and not a work item:**

- `claim_token` (31/112) needs a column on `run`. It is the monotonic fence that
  rejects a resurrected zombie worker's late write; `lease_expires_at` and
  `heartbeat_at` cover liveness but not compare-and-swap. This is the last
  P4 blocker.
- `last_heartbeat` (31/112) is already `run.heartbeat_at`.
- `job_ids` (31/112) is already `run.ticket_id`, inverted.

**Become history rows, because each is "an actor did a thing at a time":**

- `scoped_by` (112/112: `operator` | `agent` | `agent:chat`) -> a `ticket_event`
  at creation. It is who scoped the ticket, which is an event, not a property.
- `evidence` (85/112) is a list of `{kind, ref}` notes, appended over a ticket's
  life -> `ticket_event` rows. It is a worklog and it has always been one.

**Given `jsonb`, because it is genuinely structured and never queried:**

- `acceptance` (5/112) is `{kind, spec, criteria[{desc, cmd, expect}]}`. Real
  nesting, low occupancy, no filter or sort reads it. A column per key would be
  modelling for its own sake.

**Becomes an edge:**

- `discovered_from` (2/112) names another ticket -> `ticket_link` with
  `kind = 'discovered_from'`.

**Already has tables:** `labels` (18/112) -> `label` + `ticket_label`.

## Stages

Each is shippable and verifiable alone, in this order.

1. **Schema.** The columns and edges above, one migration. `run.claim_token`
   included, which unblocks P4.
2. **Writes.** `_persist` writes Postgres as the store and regenerates
   `TODO.md`. `.tasks.json` stops being written. `db/ticket_sync.py` becomes the
   writer rather than a mirror: it is the same diff-and-apply code, and it keeps
   producing `ticket_event` rows, which is how field-level history becomes real.
3. **Reads.** `_load` reads Postgres. `services/tasks.py` keeps every signature,
   so its 18 backend callers, 3 scripts and 25 test modules are untouched.
4. **Cleanup.** Delete `reconcile` and `import_markdown`. Point the two direct
   file readers (`liveness_watchdog.sh`, `dispatch_triggers.sh`) at the CLI.
   Rewrite `wkshp_propose_task` to file through the store instead of appending
   to `TODO.md`. Delete `db/shadow.py`, which has no subject once there is one
   store.

## What stays a file

`TODO.md`, as a pure export, regenerated on write and never read back.

The last `.tasks.json` is left on disk at cutover as a backup artifact and stops
being updated. It is not deleted in the same change that stops writing it.
