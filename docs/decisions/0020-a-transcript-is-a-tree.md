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

## Which bubble carries the control

Operator question, 2026-08-13, and the answer is mostly about which ones do not.

**A reply carries it, and the action keeps it.** "This answer was fine, the
conversation after it was not." Anchor is the reply itself.

**Your own message carries it, and the action takes it back.** The anchor is its
PARENT, so the chat ends just before you typed and you can say it differently.
Keeping your own message and continuing from it would leave you typing into your
own last word, which is not what pointing at it means. The label differs by role
for the same reason: one word doing two jobs would have to be learned from the
outcome.

**Only the LAST block of a log event carries it.** An interrupted reply renders
as several blocks from one event, so they all resolve to the same anchor.
Putting a control on each would show three identical buttons that look like
three branch points and are one.

**The trailing reply carries NOTHING.** Revised 2026-08-14. This first shipped
as a control that was present-but-disabled on the last message, and the operator
asked the only sensible question of it: "why would it be there for the last
message if nothing else is after?" There was no good answer. A disabled control
promises the action exists here and is unavailable right now; on the last reply
it is the identity operation and would never be available.

The rule underneath, which the special cases now fall out of, is one question:
**would going back to this message produce a conversation different from the one
on screen?** For a reply that is `m1..mi`, different only while it is not last.
For your own message it is `m1..m(i-1)`, always one shorter, INCLUDING when it is
last. So "the last bubble has no control" is not the rule, it is what the rule
produces for a trailing reply; a trailing message of your own keeps its control,
and taking back what you just sent is among the most useful cases in the feature.
It lives in `frontend/src/components/chat/branchable.ts`, executable and tested.

**The branch LIST is not on the bubble at all.** It was, as the second half of a
per-message dropdown, and that was the only reason the trailing reply still
needed a control. A list of the thread's branches is thread state: identical
under every bubble, describing none of them, and repeating it per message is the
same error as painting a constant. It moved to the toolbar beside the other
thread state (`BranchPicker`), which also answers "which line am I on", a
question that previously required opening a message's menu and hunting for a
checkmark. The bubble keeps one action and is a button.

**And it is disabled, with a reason, while a turn is in flight** — situational,
not structural, so this one IS a disabled control rather than an absent one. See
the concurrency note below.

**Pending, queued and live bubbles do not.** Not a layout decision: a pending
send is not on the server, a queued one has not been sent, and a live turn has
not committed. None of the three is a point you could return to, because none of
them is in the conversation yet.

**The head is offered but disabled.** Going back to the message the chat already
ends at changes nothing, so the item says so rather than accepting the click and
appearing to do nothing. The picker still opens, because that is the other
reason to press it.

## The tenant lookup, and a lesson about test environments

The first cut of this shipped with the control never rendering at all, and the
cause was one line: `services/tenancy.py` resolved the acting organization
through `owner_session()`. **The backend deliberately does not hold the owner
credential** (402e96cbbb79 moved it into a file the service unit does not load,
so a request handler can never run as a role row security does not apply to).
Every resolution raised in the running process, `branch` came back null, and the
control was skipped.

It passed under pytest because the test shell had both DSNs exported. That gap
is the whole lesson: **anything the serving process must do has to be tested in
the serving process's environment.** `test_the_tenant_resolves_without_the_owner
_credential` now removes the credential before asking.

The fix is `workshop_resolve_tenant`, a `SECURITY DEFINER` function (migration
d4e17c9b3a55) taking one integer and returning one `(org_id, user_id)` pair. It
cannot be a plain SELECT: the three identity tables are RLS-protected by
policies keyed on `app.current_user` and `app.current_org`, and this lookup is
what PRODUCES those values, so with neither set all three read as empty. That is
the standard bootstrap cycle and a narrow definer function is the standard
answer to it, rather than a wider policy or a privileged connection.

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

## What was wrong in the first pass (2026-08-14)

Four defects, found by reviewing the control the operator was asking about.
Recorded here because three of them were invisible from the screen.

**The model never saw the fork.** The sync endpoint filtered the transcript by
the branch from the day this shipped; `chat_store._fold`, which builds the
prompt, replayed the whole log and never asked. So after going back, the operator
saw the line he had returned to and the model was still handed everything he had
branched away from, including the exchange he went back specifically to remove.
`chat_branches.hidden_event_ids_sync` was written for that caller, names it in
its docstring, and had never been called by anything. Now wired, and boundaries
are filtered too, so a `/clear` performed on an abandoned line stops cutting the
line the operator returned to.

**Branching was not in the concurrency model.** Nothing on the fork or switch
path asked whether the thread was busy, so an in-flight reply could commit onto
the new branch as a child of the fork anchor. Both now refuse on a busy thread
(409) rather than queueing, because the turn they would wait for is the one being
discarded. Added to the case matrix in the concurrency doc.

**Mirror writes for one thread ran concurrently.** Each parents its row to the
current head, so two at once read the same head. At the START of a thread it was
worse: the thread row is created lazily by whichever write arrives first, so the
opening events all found no row, all tried to create one, and every loser died on
`uq_chat_thread_external` and dropped its message from the tree. Measured at
three of four messages lost, with the branch head left on the first. They are
chained per thread now; different threads still overlap.

**The held-task set did not exist.** `_MIRROR_TASKS` was referenced in
`chat_log._mirror` and never defined; the NameError landed in a bare `except` and
was logged at DEBUG. `create_task` had already run by that line, so the mirror
still worked and nothing looked wrong, but every mirror write was unheld and
collectable mid-flight, which is the silently-missing row this design points at
the backfill to repair.

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
