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

Second pass (2026-08-14, same day, after a fresh-context adversarial review of
the first): the top of this list is now DONE.

- **R1 closed.** The prompt's snapshot is re-read after the claim, and its
  floor is stamped on the Turn (`prompt_through_seq`) in the same synchronous
  stretch, so "did the prompt see this message" is a seq comparison. A message
  past the floor that cannot be injected queues with `recorded=True`, which
  the drain's already-delivered check now understands (a recorded entry is in
  the log by definition and still owed an answer). History strips stored
  copies of the ask (`_without_trailing_copy`) so nothing is read twice.
- **R3 closed, plus its operation-shaped twin.** A send landing while a run is
  ending joins the queue when messages are already waiting OR an operation is
  pending, instead of taking the thread over and overtaking them.
- **R2 closed.** `mark_run_started` writes nothing for a run that no longer
  owns the thread.
- **R7 closed.** `save_thread` never deletes `_PRESERVED_ON_SAVE` keys (the
  session, summary, meter, bookkeeping) on a round-trip; deliberate deletion
  of those goes through their own clear_* calls.
- **R4 closed structurally**, no code: a mid-turn /clear now lands after the
  turn ends (ADR-0021), so the wipe always wins over the turn's marks write.
- **Review findings fixed:** landing tasks are held (`thread_ops._LANDINGS`;
  an unheld task could be collected and hold the door forever), a refused
  operation answers `busy` instead of falsely promising a deferred landing,
  the meter's live hold retires on a FAILED post-turn fetch too, naming
  re-arms one re-run when a schedule was refused in flight, and
  `injectIntoRun` drops a response for a thread no longer on screen instead
  of dispatching into the wrong store.
- **The unheld-task class swept repo-wide** (I8's other shoe): `drain_soon`
  and the branch mirror now hold their tasks like naming and the landings do.
  Remaining unheld sites are all in voice (`voice_worker.py:523,825`), which
  is another workstream's surface; flagged, not touched.

Still open, in order:

- **R8 reassessed: designed, not a race.** `_promotion_patch` is a deliberate
  lazy migration (legacy `agent:<slug>` chats drift to participant form on any
  write), guarded by scope_locked and exact-participant checks. Riding
  unrelated writes is its mechanism, not an accident. No change.
- **R10 verified closed by ordering, no change**: `reattach_running_turns`
  registers every adopted turn in `TURNS` synchronously, and
  `reconcile_threads` runs strictly after it on the same thread (main.py
  lifespan), so reconcile cannot see an adopted-but-unregistered run. The
  audit's premise assumed concurrency that is not there.
- **R9 branch mirror unordered.** Belongs to the ADR-0020 tree/Postgres
  workstream; the mirror's own backfill is the repair path meanwhile (its
  tasks are now held, so collection cannot silently drop a row).
- **Voice turns during a pending operation** proceed rather than queue
  (deliberate: a spoken turn cannot wait 30s), at the cost of one uncompacted
  voice turn; the landing still serializes behind the voice run via the claim,
  found by `wait_quiet`'s 60s re-check since a voice shell Turn's `finished`
  never fires.
- **A deferred landing that fails is log-only.** The UI said "then it lands";
  a failed landing (rare: the writes are local) leaves the queue to drain on
  the pre-fold prompt with no visible signal.

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
