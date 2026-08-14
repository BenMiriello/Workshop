# The chat concurrency model

Date: 2026-08-14. Status: current. Companion decision:
[ADR-0021](../decisions/0021-a-thread-admits-one-operation-at-a-time.md).
Supersedes nothing; it names the model the existing pieces were converging on
and closes the gaps between them. Read with ADR-0018 (order is recorded),
ADR-0020 (a transcript is a tree), and `docs/chat-state-ownership-2026-08-12.md`
(the server owns the conversation).

This document exists because the operator asked why chat ordering and state
break no matter how many times they are fixed. The answer, measured across
2026-06-22 to 2026-08-14 (three audits: the fix-history sweep, the backend
write-path inventory, the frontend state map), is not one bug. It is that the
system had strong rules for DATA (append-only log, monotonic seq, recorded cut
tokens) and almost no rules for TIME: what may happen concurrently with what,
and which writer wins when two facts collide. Every recurring symptom lands in
one of ten failure classes, and eight of the ten are time, not data.

## 1. The entities, and who owns them

| Entity | Storage | Owner / writer discipline |
|---|---|---|
| The log | `log.jsonl` per thread | append-only, seq monotonic, one writer path (`chat_log.append_sync`), never rewritten |
| Thread meta | `meta.json` | patched via `patch_meta_sync` (atomic rename); transaction safety rests on writers being await-free on one loop |
| The run | `meta.active_run_id` + `TURNS[id]` in memory + `live.json` | claimed under the per-thread lock; `Turn.finished` is the awaitable end |
| The operation (fold/clear) | `services/thread_ops` registry, in-process | ONE at a time; writes land only on a quiet thread (ADR-0021) |
| The queue | `queue.json` | server-owned; drained by `chat_drain` when the thread is idle AND no operation pending |
| Drafts | server `drafts.json` + client localStorage mirror | newest ts wins, tombstone on send, fenced against empty-write resurrection |
| Context reading | `meta.context_tokens` | fenced: a measurement lands only while the thread still owns the session it measured |

## 2. The invariants

Numbered so a future fix can say which one it serves. I1 to I4 existed and
hold; I5 to I8 are what this pass added.

- **I1. Order is recorded, never reconstructed.** The log's seq is the order;
  a mid-turn message's position is a recorded cut token (ADR-0018). No
  arithmetic at render time.
- **I2. The server owns the conversation.** The browser owns exactly one
  thing: a request that has not landed yet (`chat-state-ownership-2026-08-12`).
- **I3. A message the operator sent is the system's responsibility** from the
  moment the server accepts it: durable, visible everywhere, delivered or
  visibly queued. Never only in a browser.
- **I4. One run per thread**, claimed under the thread lock, no await between
  check and claim.
- **I5. One operation per thread, landed quiet** (ADR-0021). Admission queues
  behind a pending operation; the drain refuses during one; landing waits for
  `Turn.finished`.
- **I6. A fact is fenced to what it describes.** A measurement of a session
  lands only while the thread owns that session. The general form: any
  write-back computed before an operation must lose to the operation.
- **I7. Live state that the client renders comes off the stream or the
  snapshot, and the stream must carry everything the meter of it needs.** The
  context meter was frozen mid-turn because the reading existed only in server
  memory; now it is a stream event (`context`) folded into the store like
  every other delta.
- **I8. One background task per (thread, purpose).** Auto-naming had a task
  per message; now the registry is keyed by thread. Same rule for any future
  fire-and-forget.

## 3. The case matrix

What happens for each action, in each thread state. "queue" always means the
durable server queue, drained in order when the thread is next idle.

| Action ↓ / State → | idle | run live | operation pending |
|---|---|---|---|
| send | new run | inject into the run (or queue if it takes no input / is a command) | **queue** (was: new run against the pre-op prompt) |
| send, queue non-empty | queue (nothing overtakes) | queue | queue |
| stop | no-op | server stops the run, commits what exists, tagged `stopped` | no-op on the op; a pending landing proceeds when quiet |
| /compact | summarize, land inline | summarize now, **land when the run finishes** (`deferred: true`) | refused politely (`deferred: true`, folded 0) |
| /clear | land inline | **land when the run finishes** (`deferred: true`) | refused politely |
| rename / rescope | immediate (log event) | immediate; does not touch the run | immediate |
| restart | nothing to do | turn adopted on the way back up | op dies with the process; log coherent; compact again |
| turn finalize | commit reply, drain queue | n/a | commit, then the landing task fires, then the drain |

Ordering guarantees that fall out:

- A reply always commits BEFORE any boundary that was requested during it.
  "Messages, divider, reply answering them" is no longer producible.
- A message sent during a fold is answered after the fold, on the folded
  prompt, and renders below the divider, which is where it actually lives.
- The meter can no longer show a dead session's number, and it moves during a
  turn, from the model's own per-step usage.

## 4. What was found, and what landed today

The three audit reports are in this session's transcript (searchable); the
failure taxonomy in brief: (1) two owners of one fact, (2) a turn is an
interval stored as a point, (3) triggers detached from their events, (4)
locating records by position not identity, (5) read-modify-write across
awaits, (6) stale server answers adopted, (7) **fixes claimed but never
shipped or verified**, (8) acceptance confused with delivery, (9) optimistic
state never reconciled, (10) blank rendered from the wrong flag.

Class 7 deserves its own sentence: the "context meter lag" fix was described,
recorded as committed in d0bac68, and never written; `aebeaff` found an edit
that had silently matched nothing; `7cab15f` found an audit asserting a
renderer that was never built. When a fix is claimed against the tested layer
and the bug lives in the untested one, the claim is sincere and wrong. That is
why the operator keeps reporting "the same bug".

Landed in this pass (each with tests):

1. **ADR-0021 serialization**: `services/thread_ops`, compact/clear split into
   compute + quiet landing, `_send` and `chat_drain` gates, deferred copy in
   the client (`test_thread_ops.py`, 10 tests).
2. **Context measurement fence** `measured_session` on
   `set_thread_context_tokens`; finalize passes the turn's session
   (`test_context_meter_fence.py`, 5 tests).
3. **Live context meter**: `context` stream event on change
   (`build_streaming._ingest`), `runContext` store action, meter prefers the
   held live reading and retires it on the post-turn fetch.
4. **Draft resurrection killed at the root**: `clearForSend` updates
   `draftRef`, and an empty write no longer deletes the clear tombstone
   (`draftSync.ts`). This was "my sent message is still in the box days
   later" and "a new chat opens with the last new chat's message": the
   attachment-clear on every send re-published the sent text from a
   render-stale ref under the pre-thread key.
5. **Naming task dedup**, one per thread (`chat_naming.schedule`).
6. **Divider dep fix**: `state.cut` joins the committed-transcript memo deps.

## 5. The remaining plan, in order

Backend, from the write-path inventory's numbered race windows (details and
file:line in the inventory):

- **R1 claim-to-spawn delivery hole.** A message filed against a claimed but
  not-yet-spawned run is in the log, not in the prompt, not injected, not
  queued. Fix: on the "claimed but not spawned" branch, enqueue with an
  `already_recorded` marker the drain understands, or assemble the prompt from
  a fresh read inside the claim.
- **R3 finalize window jumps the queue.** Between `_requeue_unread` and the
  commit, a new send takes the inject branch and overtakes requeued messages.
  Fix: the queue-first check should also fire when the run `is_ending`.
- **R4 `turn_marks` resurrected after /clear.** Same class as the meter; fence
  it the same way (I6).
- **R2 `mark_run_started` unconditional write outside the lock; R7
  `save_thread` deletes fields from a stale snapshot; R8 `_promotion_patch`
  rides unrelated writes; R9 branch mirror unordered; R10 startup reconcile
  vs adoption.** Each small; fix in that order.
- **Voice turns** must check `thread_ops.pending` when they start runs.

Frontend, from the state-map seams:

- **The transport harness.** The six wiring modules (~1,940 lines) have zero
  tests, and every regression in this history lived there. Build the fake
  transport (scripted SSE frames, sync answers, clock) driving the real hooks;
  replay send-during-turn, stop-mid-stream, reattach-after-drop,
  switch-mid-request, fold-mid-turn. This CHANGES the testing rule in
  `frontend/CLAUDE.md` ("no DOM/render harness... the target is the pure
  rule"): the pure-rule policy stays for components, but the hook layer gets
  the harness, because that is where the bugs are. Operator has already asked
  for this in substance; treat the CLAUDE.md edit as part of the work.
- **Request stamps** on `/context` and thread-list fetches (seams 3, 12), a
  thread guard on `injectIntoRun`'s response (seam 9), and the
  `runReattached`/sync inversion (seam 7).
- **`FoldingNote` placement** (seam 13): during a deferred fold the note
  should say "lands when the reply finishes" and sit with the divider's
  future position, driven by server state rather than one tab's POST.

Structural, already decided elsewhere: finish ADR-0020 (read path from the
tree) which deletes `interleave_history` and the last render-time ordering
code, and with it most of class 2.

## 6. How to not regress this

- A new writer of thread state names its invariant (I1 to I8) in a comment or
  it is wrong by default.
- A new long-lived action on a thread is a run or an operation. If it is
  neither, it does not get to write.
- A fix to any of this ships with a test at the seam it fixes, and "committed"
  is checked against the diff, not the summary. Class 7 is the meta-bug; the
  reviewer gate exists for it.
