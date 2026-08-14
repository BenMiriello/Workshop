# Chat storage: one store, Postgres

Status: **plan, being implemented.** Written 2026-08-14.

This replaces an earlier draft of the same filename that framed the move as
hinging on an availability trade. That framing was wrong and the operator said
so: "isn't that every web application that is based off of a database, which is
almost all of them." It is. A database-backed app depends on its database. Chat
is not special, and that is not a reason to keep two stores.

## Why there are two stores today

Order of construction, not design.

The JSONL log came first, and for a real reason: it replaced a whole-document
JSON store where every write was a read-modify-write of the entire thread. Three
paths broke that; the worst read a thread, spent ~30s summarizing, and wrote the
snapshot back, deleting whatever the operator had typed meanwhile. An append
cannot do that.

Postgres landed on 2026-08-13, and chat was pulled forward into it a day later
for branching, because a transcript that branches is a tree and a flat file
cannot express one. Rather than move the whole thing at once, messages were
MIRRORED: log first (authoritative), database second (best effort).

That mirror is migration scaffolding. It was never a design, and describing it
as one is what made this confusing.

## What is wrong with keeping it

Not availability. Duplication.

**One rule, four implementations.** "Where does a message that interrupted a
reply belong" is currently answered by `interleave_history` (the prompt),
`_fmt_for_summary` (the summarizer), `voice_worker._thread_seed` (voice), and
`cutPieces`/`legacyPieces` (the browser). They must agree and they do not: the
prompt honours only the recorded tokens while the screen also resolves the legacy
index, so the model and the operator currently disagree about 33 replies.

**Every bug in the branch feature was a place the two stores disagreed.** The
prompt read the abandoned line because `_fold` never applied the branch filter.
The fork anchored at the end of an interrupted reply because the client can only
name a log event and one event is several rows. None of those are branch bugs;
they are seam bugs.

**The tree is built by a best-effort background write**, so it can silently miss
a row. That is survivable while it is only an index and unacceptable once it is
the store, which is why the write path is step 1 and not step 3.

## The target

Postgres is the store. `chat_message` rows are the transcript. The log is
retired.

The schema already supports this: `chat_message` has `parent_id`, `turn_id`,
`role`, `kind`, `content`, `segments`, `meta`, `client_id`, `external_id`, `seq`,
and `chat_turn` carries the turn-level facts (model, timings, error, timeout,
stopped). `chat_repo._row` already emits the exact event shape the client
consumes today, including putting a turn's outcome on its last stretch only.

## The one genuinely tricky part, and why it is already solved

An interrupted reply must become SEVERAL rows, threaded with the messages that
interrupted it: `stretch0 → msg → stretch1 → msg → stretch2`. `parent_id` is
immutable by trigger (that is what makes a cycle unbuildable), so those rows have
to be created with the right parent the first time. No re-parenting.

The obstacle looks fatal at first: the interrupting message arrives while the
reply is still streaming, so at the moment it needs a parent, the stretch it
belongs after does not exist yet.

**It does exist, as a decision, and the server already makes it.**
`build_streaming.cut_here` runs the instant an interrupting message arrives and
calls `seal_open_text`, which closes the open text run so nothing written
afterwards can merge backwards into it. That call is the server stating "this
stretch of the reply is finished, and a message goes next". It is exactly the
point at which a row can be opened with its parent already correct.

So:

- **At seal** (`routes/chat._file_during_run`, between `_cut_point` and the
  append): open a stretch row for the turn, parented to the current head, and
  move the head. The interrupting message then parents to it by the ordinary
  rule.
- **At commit**: split the final segment list on the recorded cut tokens, fill
  each opened stretch row's content, and insert the last stretch.
- **When nothing had been emitted** (`cut_head`): no stretch is opened, and the
  message parents to the pre-run head. That is the "the run's own start is the
  floor" case and it comes out right for free.

Structure is written when it becomes known; content is written when it is final.
`parent_id` is never mutated, only `content`/`segments`, which are not immutable.

**This is NOT the reverted `revises:` mechanism.** That claimed a slot for the
reply at turn START, before any output, which forced every mid-turn message
below the whole reply and is precisely the bug that took five rounds of
corrections. A slot claimed at SEAL time is the opposite: it is claimed at a
moment that is already true, because output has demonstrably been emitted and a
message demonstrably follows it. Do not collapse the two when reading this
later.

## Stages

Each stage is shippable and verified on its own.

**1. The tree becomes complete and true for live traffic.** Write `chat_turn`
rows (nothing has ever written one outside the backfill). Open a stretch row at
seal; fill and close at commit. Stop stripping the positional meta, since the
structure now carries it. Add a completeness check (every log message event has a
row) and a repair. The log is still authoritative for reads throughout, so
nothing user-visible changes and the stage is verifiable by comparing the tree
against the log for real threads.

**2. Reads come from rows.** `thread_events` serves `chat_repo.transcript`
instead of log events plus the hidden-set filter. `keep_visible` and the
hidden-set go. The client renders rows directly, which deletes `replyBlocks`,
`cutPieces` and `legacyPieces`: each stretch is already its own row, so there is
nothing to split. The sync cursor stays `seq` (a per-thread insertion counter,
which is exactly what an incremental cursor wants); render order is the parent
chain, which the client walks. Branch switches keep the existing reset.

**3. The backend reconstructions go.** `interleave_history`, `_fmt_for_summary`
and the voice seed read rows in path order. Four implementations of the ordering
rule become zero, because order stops being derived anywhere.

**4. The log is retired.** Writes go to Postgres in the request. `chat_store`
is the facade in front of `chat_log` for nearly every caller, which is what makes
this tractable; the direct external callers are few. The per-thread asyncio lock
is replaced by the database's own transactions. Decide at that point whether to
keep writing a log as an export artifact; it is no longer a store either way.

## What stays

`live.json` (the in-flight turn's draft) stays a file. It is rewritten many times
a second while a turn streams, it is discarded when the turn commits, and it is
the one thing here that genuinely does not want a transaction per keystroke. It
is not a store of record and never was.
