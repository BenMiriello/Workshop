# 0020. A transcript is a tree, and it lives in PostgreSQL

- Status: **accepted**
- Date: 2026-08-13
- Extends: ADR 0018 (transcript order is recorded, not reconstructed) to the
  storage layer, and ADR 0019 (work data lives in PostgreSQL) to chat, which
  that ADR's plan had sequenced last and separately (P6).
- Operator decision, 2026-08-13: be able to go back to a point in a chat and
  carry on from there; and chats are to be stored in PostgreSQL in a normal
  schema.

## Context

**The feature is not expressible in the current store.** The chat log is
`.runtime/users/<owner>/chat/<thread>/log.jsonl`: append-only, ordered by a
monotonic `seq`, with no revision, no delete and no parent. Its own module
header says so outright, and `chat_store.save_thread` states that "editing a
stored message is not expressible and is not attempted". A previous `revises:
<seq>` mechanism was built and deleted. Going back to an earlier point is not a
feature that could be layered on top of that; it is a different data model.

**The ordering defect and the branching feature are the same problem.** ADR 0018
established that a message sent while a reply is streaming belongs INSIDE that
reply: after the work already done, before the work not yet done. The log
records that as a token pair (`meta.cut_id` on the message, `seg.cut_after` on
the segment it arrived after) and three separate readers re-derive the split on
every render. That is a reconstruction, and reconstructions of this exact kind
are what ADR 0018 was written to stop one layer up.

Both fall out of the same missing relation: **a message does not record what it
came after.**

**Measured before deciding.** 71 thread directories, 1,857 log events, 23,521
segments. Of 90 mid-run messages, 11 carry a cut token (the mechanism is one day
old), 31 carry the legacy `at_unit` index and 48 carry only `at_segment`. Five
replies have a `content` field that disagrees with the segments stored beside
it. Nothing in the current model has an opinion about any of this.

## Decision

**A transcript is a tree, stored in PostgreSQL, in four tables.**

- `chat_message` is a node. **`parent_id` IS the render order.** The visible
  transcript is the path from a branch's head up to the root, reversed.
- `chat_branch` is a **named pointer to a message**, plus the message it forked
  from. It stores no membership, so it cannot disagree with the tree about what
  the conversation is. This is git's ref model.
- `chat_thread` holds identity and configuration, and one mutable pointer,
  `current_branch_id`, which decides what the transcript is.
- `chat_turn` is one run. It exists because an interrupted reply is several
  messages and they need something that says they were one reply.

Three consequences are the point rather than side effects.

**`seq` and render order become different numbers.** `seq` stays, as an
insertion counter and the sync cursor. It stops being the order. They were the
same number in the log, and that is precisely why placing a mid-reply message
needed a second mechanism beside it.

**An interrupted reply is stored split.** A reply interrupted twice is three
assistant rows with the two messages threaded between them. `cut_id`,
`cut_after`, `cut_head`, `at_unit`, `at_segment`, `during_run` and `injected`
then describe nothing that is stored. The reconstruction runs exactly once, in
the backfill, and its output is rows.

**There is ONE branch verb, not two.** "Return to here" and "branch from here"
are the same operation in a store where nothing is deleted: both create a ref at
that point and make it current, and the line being left keeps its own ref and
stays listed. Offering both would be two names for one act, and the operator
would reasonably expect them to differ. The confirmation therefore does not warn
about losing anything; it says that nothing is lost, which is the fact a reader
needs and cannot otherwise know.

### What did not move

The per-run scratch stays in files: `live.json` (the in-flight draft, rewritten
roughly every 0.4s and deliberately not fsynced), `queue.json`, `drafts.json`,
`prefs.json`. None of it is a record of what was said, all of it is rewritten
constantly, and none of it needs to survive in the sense the transcript does.
The line is: **the record of what was said is in the database; the scratchpad of
a turn in flight is a file.**

### Segments are JSONB, against this schema's own default

`db/models.py` argues against JSONB payloads, and that argument came from
`ticket_event`, whose fields ARE queried ("who changed the priority, from what").
It does not carry here. Nothing filters, sorts or joins on a segment field; a
live turn mutates the list several times a second, so a row per segment turns
one in-memory append into ~20 writes; and the one genuinely relational field on
a segment, `cut_after`, is deleted outright by threading the message into the
tree. A GIN index is available if searching tool calls ever becomes real.

### Integrity is same-thread, not just same-org

Migration 402e96cbbb79 established composite `(id, org_id)` foreign keys after
reproducing cross-tenant references live: Postgres runs referential-integrity
checks with row security OFF, so a policy constrains the label on a row and says
nothing about what it points at. Inside a conversation the property that matters
is narrower: a parent, a branch head or a turn reference must name a row in the
SAME THREAD. That implies same org, because the thread pins it. So intra-chat
foreign keys are composite on `(id, thread_id)`.

`parent_id` is immutable, enforced by trigger. Nothing re-parents a message:
branching creates rows and moves refs, it never rewrites history. Immutability
is cheaper than cycle detection and strictly stronger, because a cycle can only
be built by an UPDATE.

## Migration

`db/chat_backfill.py`, idempotent and re-runnable, reads the files and writes
none. Both split paths are implemented, because the operator's transcript is
rendered with both: the cut TOKENS where they exist, and the legacy
`at_unit`/`at_segment` index for the 79 of 90 mid-run messages recorded before
tokens existed. Skipping the second would have silently flattened 33 replies
that currently render split.

Verified against the live data, not a fixture: 63 threads, 1,352 messages, 562
turns. **Conservation holds on 63 of 63** (the rendered text is identical per
role), and **every ordering difference from the current prompt-side order is
explained**: each is a mid-run message moving into the reply it interrupted,
with zero unexplained moves.

That difference is itself a finding. `chat_context.interleave_history` honours
only the cut tokens, while the rendered transcript also resolves the legacy
index, so **the model's history and the operator's screen disagree about 33
replies today**. The backfill follows the screen, which makes them agree.

### The read path, and why it is safe

The log remains the durable write and is unchanged. `chat_log.append_sync`, the
one funnel every append goes through, additionally mirrors the event into the
tree, fire-and-forget: the log line is already fsynced, and a database outage
must not stop the operator sending a message.

The sync endpoint returns the current branch alongside the events and filters
out events on branches this thread is not showing. **The filter is the HIDDEN
set, not an allow-list**, so an event the tree has never heard of still renders:
one failure mode is a stale message on screen, the other is a message the
operator just sent vanishing, and only the first is recoverable by looking
again.

**For a thread nobody has branched, the filter is `None` and nothing changes.**
That is the whole safety argument, and it needs no feature flag: the mechanism
is inert until the feature is used.

## Consequences

**Gained.** Going back to a point in a conversation, with nothing destroyed to
do it. An order that is stored rather than derived, in one place instead of
three. Referential integrity on the transcript. Tenancy on chat data, which had
none: it was partitioned by directory path.

**Cost.** Chat now depends on a database process for branching (not for
rendering). Two copies of the message text exist during the bake: the log and
the tree. The log is not yet retired, and until it is, the tree is authoritative
for STRUCTURE and the log for CONTENT, which is a seam that must be closed
rather than left.

**Not done here, and it is the next step.** The transcript read still comes from
the file; only branch membership comes from the database. Until
`chat_store._fold` reads rows, a branch point inside a reply cannot be
expressed, so the fork anchor resolves to the END of an interrupted reply. Three
readers (`interleave_history`, `_fmt_for_summary`, the voice path) still
reconstruct order from tokens and should be deleted, not adapted, once the read
path moves.

## Alternatives considered

**Keep the JSONL log and add a branch index beside it.** Cheapest, and it was
the shape this started as. Rejected: it makes structure and content two stores
that must agree, with no constraint able to enforce it, and it cannot express a
split reply at all.

**A pure message tree with no branch table.** Siblings give branches for free,
which is the ChatGPT model. Rejected for one concrete state: going back to a
point and not yet having typed. That branch has no message to represent it, and
it is the most common state the feature produces, not an edge case.

**Materialized path or nested sets instead of an adjacency list.** Faster reads
of a subtree. Rejected: neither is needed (the path walk is an indexed climb of
a few hundred rows) and both denormalize the one relation this design is about.

**Fork into a separate chat rather than a branch in place.** A real feature and
a different one. The ask was branches within a conversation, and adding a second
destination would double the surface for an operation nobody asked to be able to
do twice.
