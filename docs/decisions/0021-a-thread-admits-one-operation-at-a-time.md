# 0021. A thread admits one operation at a time, and operations land on a quiet thread

Date: 2026-08-14
Status: accepted

## Context

A chat thread has two kinds of long-lived work: a RUN (a turn being driven by
the CLI) and an OPERATION on the stored conversation (a `/compact` fold, a
`/clear` cut). Runs serialize against each other through the claim taken under
the per-thread lock in `build_streaming.claim_run`. Operations serialized
against nothing.

The measured incident (2026-08-14 03:20, thread `57e6c4b8`): the operator
pressed compact; the fold spent ~22 seconds in its summarizer; two messages
sent during that window started a turn against the pre-fold 467k-token prompt;
the boundary then landed mid-turn at seq 33; the reply committed one second
later at seq 34, on the far side of the divider from the messages (seq 30, 32)
it was answering; and the turn's finalize wrote its pre-fold context
measurement back over the meter the fold had just cleared, so a freshly
compacted chat read 47% for ten minutes.

The client had a guard (`useCompaction` holds the composer while its POST is in
flight), but it was one tab's React state. Any second surface, and several
same-tab paths, went around it. The 2026-08-12 audit
(`docs/chat-state-ownership-2026-08-12.md`) already established the rule that
the server owns the conversation; this is that rule applied to admission.

A second, quieter defect had the same shape from the other side: `/clear`
computed its cut from its own read of the thread, so a message appended by a
running turn between the read and the boundary write fell above the cut and
silently vanished from every later prompt.

## Decision

1. **One operation at a time.** `services/thread_ops` keeps an in-process
   registry, thread id to operation name. `begin` refuses a second operation;
   the route answers `busy: true` for a refusal, distinct from
   `deferred: true` (computed, lands when quiet): a refusal computed nothing,
   and reporting it as deferred would promise a landing that never comes.
   Landing tasks are HELD in the registry; an unheld asyncio task can be
   garbage-collected mid-wait, which would leave the registration in place
   and the thread queueing forever.

2. **Compute free, land quiet.** An operation's expensive read-only half (the
   summarizer call) runs while the thread lives its life. Its WRITES (summary,
   cut, boundary event, session detach) happen only when no run is live. If a
   run is live when the writes are ready, they move to a background landing
   task that waits on `Turn.finished`, and the endpoint answers
   `deferred: true` so the client can say so.

3. **Admission respects the pending operation.** While an operation is
   registered, the send path routes a new message onto the durable queue
   (`routes/chat._send`) exactly as it does behind a waiting queue, and the
   drain refuses to start a turn (`chat_drain.drain`). `end()` kicks the drain,
   so queued messages go out, in order, against the post-operation prompt.
   Injection into an already-running turn stays allowed: that turn precedes the
   operation and the landing waits for it anyway.

4. **Facts are fenced to what they describe.** A context measurement carries
   the session it measured (`chat_store.set_thread_context_tokens
   measured_session=`), and the write is dropped when the thread no longer owns
   that session. This is the general remedy for the "late finalize resurrects
   what an operation just cleared" class; `turn_marks` after `/clear` is the
   known remaining instance.

The registry is in-process memory, deliberately. The backend is one process;
an operation is one HTTP request plus at most one landing task, and a restart
kills both halves together, leaving only the append-only log, which is always
coherent. A fold deferred past a restart never lands; the operator compacts
again. Persisting the flag would recreate the stale-flag class this deletes.

## Consequences

- A message sent during a fold is accepted, visible, durable, and answered
  after the fold, on the compacted prompt. It can no longer race the fold or
  defeat it.
- A boundary event now always sits at a run boundary in the log. The transcript
  reading "messages, divider, reply-that-answers-them" is no longer producible.
- Compact and clear during a long turn become "lands when the reply finishes"
  rather than immediate. That is the honest ordering; the client says it in one
  line.
- Anything that starts turns without going through `_send` or the drain (voice
  turns, future callers) must check `thread_ops.pending` or inherit the race.
  Voice is the known gap and is on the follow-up list in
  `docs/platform-redesign/chat-concurrency-model-2026-08-14.md`.

Pinned by `backend/tests/test_thread_ops.py` and
`backend/tests/test_context_meter_fence.py`.
