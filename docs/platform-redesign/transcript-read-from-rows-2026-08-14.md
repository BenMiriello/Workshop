# Moving the transcript READ onto the tree

Status: **plan, not built.** Written 2026-08-14 after fixing the four defects
recorded in [ADR 0020](../decisions/0020-a-transcript-is-a-tree.md). It carries
one decision that is the operator's, not mine, and it is stated first because
everything else depends on the answer.

## Where things stand

The WRITE path is the append-only JSONL log, mirrored into Postgres. The READ
path is still entirely the log. The tree is consulted for exactly two things:
which branch is current, and which log events to hide.

That split is doing real work. Branching degrades to "the chat renders, it just
cannot be branched" when Postgres is unavailable, and rendering a chat has no
database on its latency path at all.

## The decision: what a Postgres outage costs

Today it costs branching. After this migration it costs **the chat**: no
transcript renders, on any device, until the database is back.

That is not a footnote, it is the whole trade. Everything below is
straightforward engineering; this is the part worth deciding deliberately.

Three honest answers:

**(a) Do it, and accept the coupling.** Simplest end state. The three order
reconstructions delete rather than surviving as a second implementation of the
same rule, the cursor becomes tree-native, and mid-reply branch points become
expressible. One store, one order, no agreement to maintain.

**(b) Do not do it.** Keep the log as the read path forever and treat the tree as
a structural index over it. Costs: `interleave_history`, `_fmt_for_summary`, the
voice seed and the client's `legacyPieces` all stay, four implementations of one
ordering rule that must agree, plus permanent last-stretch-only fork anchoring.

**(c) Do it with the log as a fallback read.** Serve rows; fall back to the log
when the tree is unreachable or incomplete for a thread. Keeps today's
availability. Costs: both readers stay alive, so (b)'s duplication is not
retired, and the fallback path is exercised approximately never, which is how a
fallback rots.

**Recommendation: (a), and not yet.** The coupling is acceptable because the
database is local, on the same box, with no network between it and the backend,
and because everything else in the platform is heading onto it anyway. But it
should land after the mirror is provably complete, not before: today the tree is
built by a best-effort background write, and moving the read onto it while it can
silently miss a row would make a lost mirror write into a lost message on screen.
That is a much worse failure than a lost branch point. Step 1 below is what earns
the right to do step 4.

## Why it is not one change

### 1. The live mirror is less expressive than the backfill, and cannot be fixed in place

The backfill splits an interrupted reply into one row per stretch and writes
`chat_turn` rows. The live mirror writes **one flat row per log event**, strips
the positional meta (`cut_id`, `cut_head`, `at_unit`, `at_segment`, `during_run`,
`injected`), and never writes a `chat_turn` at all. Nothing in production has
ever written one. Re-running the backfill does not repair it: `_load_thread`
skips any event whose `external_id` already exists.

And it cannot simply be taught to split, because **the shape of a turn is not
known until the turn is sealed**. The interrupting user messages arrive and are
mirrored while the reply is still being written; by the time the reply commits,
those rows already have parents. Splicing the reply's stretches between them
means re-parenting, and `parent_id` is immutable by trigger, deliberately, since
that is what makes a cycle unbuildable.

So the fix is a change of GRANULARITY, not of logic: **mirror a turn, at seal,
atomically** — the stretches and the messages that interrupted them, written in
true order in one transaction. A message sent during a run is not mirrored on
arrival.

That is coherent (the log is the durability guarantee and is written first; the
tree is a structural projection, and structure is a property of the whole turn)
and it has one honest cost: a process death mid-run leaves those messages in the
log and not in the tree until a backfill top-up. Safe today, because an event
with no row is KEPT by the filter. Not safe once the read comes from rows, which
is exactly why this is step 1 and the read is step 4.

### 2. The anchor has to name a row, and today it cannot

The client can only name a LOG EVENT. One event is N rows, so `_resolve_anchor`
disambiguates by picking the last (for `after`) or the first (for `before`). That
is why a fork anchors at the END of an interrupted reply, and why only the last
block of an event carries the control. It is an addressing problem created by
rendering from the log, not a storage problem: the rows exist and are individually
addressable.

Once step 1 lands, the client can send `<event_id>#<stretch>` and every stretch
becomes its own branch point, **without** step 4. Worth noting: that delivers the
only user-visible capability in this document, at a fraction of the risk. If the
answer to the decision above is (b), do this anyway.

### 3. The sync cursor is a log seq

`after` / `last_seq` are log sequence numbers. `chat_message.seq` is a per-thread
insertion counter and explicitly not the order. A cursor over a tree needs
re-specifying, or the endpoint stops being a cursor sync.

### 4. Then, and only then, serve rows

`chat_repo.transcript` already produces render-order nodes with `is_turn_tail`
correct, and `read_transcript` is the org-scoped entry point with no caller yet.
Pointing `thread_events` at it deletes the hidden-set filter, the branch-change
reset in `useThreadLog`, and all four order reconstructions.

## Sequence

1. **Mirror a turn atomically at seal.** Stretches plus interleaved messages, in
   true order, one transaction. Backfill top-up for the gap. Add a completeness
   check (every log message event has a row) and run it before trusting the tree.
2. **Address a row, not an event.** `<event_id>#<stretch>` anchors. Every stretch
   of an interrupted reply gets its own control. Independently useful.
3. **Re-specify the cursor** over the tree.
4. **Serve rows**, delete `interleave_history`, `_fmt_for_summary`, the voice
   seed, and `cutPieces`/`legacyPieces`.

1 and 2 are worth doing under any answer to the decision. 3 and 4 are only worth
doing under (a).
