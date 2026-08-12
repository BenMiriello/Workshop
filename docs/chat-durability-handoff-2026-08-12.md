# Chat durability: what was wrong, what changed, what is left

**Handoff, 2026-08-12. Read this before touching anything under
`backend/routes/chat.py`, `backend/services/chat_*.py`,
`frontend/src/components/chat/` or `frontend/src/lib/chat*.ts`.**

Companion doc: [`chat-state-ownership-2026-08-12.md`](chat-state-ownership-2026-08-12.md)
is the short version — the contract, a table of every piece of chat state and
its owner, and the rules. This one is the full account: the incident, the
evidence, every defect found, every fix, and what is still open.

Commits: `76f9c66` (durability, drafts, compaction) and `b58ed7f` (the change
stream). 42 files.

---

## 1. The incident

The operator sent two long messages and they ceased to exist. No error, no
trace, nothing to recover from the server.

Reconstructed from the backend access log and the thread on disk. Thread
`4102a2fec0384067887d76c5af007201`, times UTC:

| time | what happened |
|---|---|
| `17:43:43` | `POST /compact`. The server-side fold failed instantly with `[Errno 7] Argument list too long` and the endpoint answered 200 anyway. A `/compact` turn started. |
| `17:43:51`–`17:44:52` | A continuous run of `PUT /chat/drafts/development::4102…`. This is him typing the two messages. |
| `17:44:57`–`17:45:01` | He navigated away: launcher, projects, then chat `596c8d5c…` and its live turn. |
| `17:45:54` | The `/compact` turn finished. Nothing was bound to the thread to notice. |
| `17:57:58` | He opened the thread again **from a different address** (everything before is `2a09:bac3:…`, everything after is `2600:1700:…`). |

The two messages never appear in the log, and no POST carrying them appears in
the access log at all. He later confirmed the diagnosis himself: returning to
the original browser and reopening that chat made them send themselves.

**Why:** a message typed during a turn that reads no input was held in that
browser's `localStorage`, and delivery required a tab to be open, bound to that
thread, and awake at the moment the turn ended. The server was explicitly told
not to record it. From the code as it stood:

> It is NOT recorded here. Nothing is owed by a message that has not been
> accepted, and the client keeps it queued and sends it as its own turn the
> moment the command finishes.

That reasoning is the bug. The operator hit enter; it is owed.

---

## 2. The class

Every defect below is one thing: **client state presented as server truth.**

Each had been reported separately, sometimes repeatedly, and each had been
fixed as its own bug. The type-ahead queue alone had accumulated eleven numbered
durability invariants in `useMessageQueue.ts`, every one of them a real
incident, all of them trying to make one browser's `localStorage` behave like
storage. It cannot.

The operator's framing, which is now the contract:

> The chat state and UI and what we show there is a contract with the user. What
> you do there and what you see there and what they submit there, they assume
> and trust that that is gonna be there.

---

## 3. The design now

**The server owns the conversation. The browser renders it.**

Three rules, stated fully in the ownership doc:

1. Every fact about a conversation lives on the server. The browser holds a
   view.
2. Everything in the transcript comes from the transcript: a log event, the
   server's `active_run`, or this browser's own un-acknowledged sends. There is
   no fourth source.
3. The browser owns exactly one thing: a request that did not land.

### The queue

`backend/services/chat_queue.py` — a message the thread cannot run yet is
recorded on the thread, at `.runtime/users/<owner>/chat/<thread>/queue.json`,
written with fsync.

- Idempotent on `client_id`: a retry, a second tab, the browser's own outbox all
  arrive with the same id and queue once.
- Ordered by when the operator hit enter (`ts` from the client, `received_at` as
  tiebreak), because arrival order is the wrong answer when one request was
  slower.
- Batched the way turns are formed: everything consecutive goes as one turn, a
  single-line slash command goes alone. That rule used to live only in the
  browser (`turnBatching.ts`) and now lives where turns are formed.
- Carries attachments and per-message model/effort/permission-mode.
- A corrupt file reads as empty rather than wedging every future send.

`backend/services/chat_drain.py` — what makes it move, with no browser involved.
Triggers: a turn finalizing (`build_streaming._finalize_turn`), an external run
released (voice), a message being enqueued when nothing turns out to be running,
and backend startup. All are safe to fire spuriously; the drain re-reads the
world under a per-thread lock and does nothing unless the thread is genuinely
idle with something waiting.

The starter is injected (`chat_drain.register_starter`) because forming a turn
is `routes/chat.py`'s job and importing that from `build_streaming` would be a
cycle.

### The composer

`backend/services/draft_store.py` — a draft is everything in the composer, text
and attached files together, keyed `"<context_key>::<thread_id>"` or
`"<context_key>::new"`.

- **The server clears it on send.** The client sends `draft_key` with the
  message; the server clears exactly that key when it accepts.
- Clearing writes a **tombstone** (empty text, timestamped) rather than deleting
  the row, so a PUT already in flight from before the clear loses on ordering
  instead of resurrecting sent text.

`frontend/src/lib/draftSync.ts` — every async result is matched against the key
it *asked about*, never applied to whatever chat is on screen when it resolves.

### Compaction

- The context cut is applied where the prompt is built.
- `/compact` folds: summarize everything but the last `_COMPACT_KEEP_TAIL` (6)
  messages, store the summary, move the cut, detach the live session.
- The transcript is untouched. The summary stands in only for what the *model*
  is handed.
- A `boundary` event with `mode: "compact"` records the fold; the divider is
  drawn from the cut **the server says it is applying**, not from the raw event.

### Change delivery

`backend/services/chat_events.py` + `GET /chat/threads/{id}/stream` — a version
per thread, bumped by the three writes that change what a client renders, and an
SSE endpoint to wait on it. The client syncs the instant anything moves.

The stream carries **no content**: it says the thread changed, and the client
runs the same cursor-based `sync()` it always had. A stream carrying the
transcript would be a second answer to "what is in this chat".

---

## 4. Every defect found, and its fix

### 4.1 Reported by the operator

**A queued message existed only in one browser.** → The queue is on the thread.
(§3)

**The other device never showed messages that had been sent.** They were in the
first browser's storage; there was nothing to show. Queued messages now ride on
the sync endpoint and appear on every device.

**A sent message stayed in the composer**, and **a message already sent turned up
in the next new chat.** One cause: the client cleared the draft under the key the
composer held *after* the send, and sending the first message of a blank chat
*changes* that key (the chat becomes a thread). The clear landed on
`::<new id>`, empty anyway, and `::new` kept the text forever, so every later
blank chat adopted it. → The server clears the key it was told the message came
from.

**Text typed in one chat appeared in another.** `useDraft` applied every async
result to `keyRef.current` — the key as of whenever the request resolved. Switch
chats while a draft fetch is in flight and chat A's text was written into chat
B's composer and B's storage. → Captured keys, checked before applying.

**Compact did nothing.** Two independent causes, §4.2 and §4.3.

**Cross-device updates were up to ten seconds late.** → The change stream.

### 4.2 `/compact` and `/clear` were bookkeeping

`_assemble_full_prompt` never applied the context cut. `get_thread` returns every
message the thread has ever held plus a `cleared_through` **count**, and trimming
is the caller's job; the caller that builds what the model reads did not do it.

So `/clear` appended a boundary, dropped the CLI session, drew a divider, and the
next turn re-sent the entire history. The context meter *did* subtract the cut,
so the meter fell while nothing else did.

Measured before the fix: a thread of six exchanges, `/clear`, then a fresh
prompt — every message from before the clear still present.

`/compact` was that plus folding having been removed entirely earlier the same
day (reasonably: its summarizer had never worked, §4.3), so it only detached the
session.

**Fix:** the cut is applied in `_assemble_full_prompt`; `/compact` folds. Measured
after, on a thread of ten exchanges: conversation block 29,967 → 8,491 chars,
whole prompt 45,950 → 24,996 (the remainder is fixed persona/preamble overhead).
`/clear` empties the conversation block entirely. All 21 messages remain in the
log.

### 4.3 The summarizer could not run on anything worth summarizing

`llm_router.call_cloud` built `[CLAUDE_CLI, "-p", prompt, …]`. Linux caps a
**single argv element** at `MAX_ARG_STRLEN` = 128KB regardless of total argv
room. Every caller that summarizes something long crossed it. The failure was
swallowed by `_compact_thread`'s best-effort `except`, the endpoint answered
200, and the chat said "Compacted. Earlier messages are now a summary."

Live evidence: `chat compaction failed for 4102a2fec…: [Errno 7] Argument list
too long`, on a 397KB log, with `boundary_seq` still 0 afterwards.

**Fix:** the prompt goes on stdin (`subprocess.run(..., input=prompt)`); `claude
-p` with no prompt argument reads it there. Proven with a 288KB round trip.
This affected every large-prompt caller, not just compaction.

### 4.4 Found by audit, not reported

These came from enumerating the design rather than pattern-matching bug reports.

**Scope changes were rendered from React state.** `withScopeNotes` spliced a
"Scope changed to X" line into the transcript from component state: visible only
in the browser that flipped the picker, gone on reload, absent on every other
device. → A `scope` event in the log, rendered like everything else.

**Attached files were browser-only.** Uploaded on arrival so the bytes were safe,
but the fact that they belonged to *this composer* was React state. A reload
restored the text without the screenshot it was about and orphaned the file. →
Part of the draft.

**A spoken turn could vanish.** `voice_worker._persist_turn` set
`turn.persisted = True` *before* attempting the append, so a failed write was
never retried and the message was gone with a warning. → Marked persisted only
after the write succeeds; logged at error level.

**An ordering inversion.** In the window between a command turn ending and the
drain firing, the thread looks idle, so a message sent then started its own turn
*ahead* of messages typed earlier and still queued — recording them in the wrong
order permanently. → Nothing overtakes a non-empty queue; a send joins it.

**The drain was not idempotent.** Entries leave the queue only after the turn
carrying them is recorded (the safe order), so a crash in that window left them
queued *and* committed and the startup drain would hand the model the same
message twice. → The drain drops entries whose `client_id` the log already
carries.

**An archived chat could fire its queue.** A concurrent session made delete into
archive, with `_send` refusing archived threads (409) — but the drain reaches
`_run_for_message` directly and never passes that check. → `_start_queued_batch`
refuses an archived thread and holds the queue with a reason. Nothing is
dropped; restoring the chat restores its queue.

**The cut had two answers.** The prompt derived it from the log; the divider
derived it from boundary events. Four legacy `compact` boundaries exist in real
threads with no summary behind them (folding was removed and restored the same
day), which the server ignores — so the client would have drawn "folded into a
summary" while every message was still being sent. → One resolution
(`chat_store.resolve_cut`), cached in meta, on the wire as `cleared_through` +
`cut_mode`, feeding both.

**A whitespace-only summary counted as a successful fold** — it would have moved
the cut and left the model with neither the messages nor anything standing in
for them. Caught by a test written expecting the opposite.

### 4.5 Regressions I introduced and fixed

Recorded because they are the traps in this area.

**A full log read on every poll.** Computing the cut payload through
`get_thread` re-parsed the entire log on every sync from every tab — the exact
cost that endpoint was rebuilt to remove. Caught within the same run by
`TestTheSyncEndpointDoesNotReparseTheLog`. → Cached in meta, backfilled at
startup.

**A startup backfill that silently did nothing.** `chat_log.list_thread_ids()`
is owner-scoped and startup has no owner in context, so the first version
resolved zero threads on a box with seventy-nine and reported success. → Iterates
owners explicitly, like `chat_log.migrate_all_owners`.

**Two probe threads created in the live runtime.** I ran a verification script
against `.runtime` instead of a temp dir. Both removed within minutes; noted
here because a third session was hunting a thread-creation flood at the time and
they could have muddied the data.

---

## 5. File map

### New

| file | what it is |
|---|---|
| `backend/services/chat_queue.py` | The durable per-thread queue. |
| `backend/services/chat_drain.py` | What sends it, with no browser involved. |
| `backend/services/chat_events.py` | Per-thread change versions for the stream. |
| `frontend/src/components/chat/useSendOutbox.ts` | Durable mirror of un-acknowledged sends. |
| `frontend/src/components/chat/QueueNote.tsx` | What the waiting messages are waiting for. |
| `docs/chat-state-ownership-2026-08-12.md` | The contract and the state table. |

### Deleted

`frontend/src/components/chat/useMessageQueue.ts` (448 lines). Its durability
role moved to the server; what remained is `useSendOutbox`.

### Changed, and why

| file | change |
|---|---|
| `backend/routes/chat.py` | `_send` enqueues instead of deferring; `_start_queued_batch`; queue cancel/release endpoints; the thread stream; `_fold_thread` + `_summarize_sync`; the cut applied in `_assemble_full_prompt`; `summary` in `_build_prompt`; `_cut_payload`; `draft_key` and `sent_at` on the wire; a `scope` event on rescope. |
| `backend/services/chat_store.py` | `set_summary`, `resolve_cut`, `backfill_cuts`, `cut_state`; `_set_cut` takes a mode; `_fold` honours a `compact` boundary only when a summary exists. |
| `backend/services/chat_log.py` | `scope` event kind; change notification on append and meta patch. |
| `backend/services/build_streaming.py` | Drain hooks in `_finalize_turn` and `release_external_run`. |
| `backend/services/draft_store.py` | Tombstones, attachments, `clear_for_thread`. |
| `backend/services/llm_router.py` | Prompt on stdin. |
| `backend/services/voice_worker.py` | `persisted` set only after a successful write. |
| `backend/main.py` | Startup cut backfill and queue drain. |
| `frontend/.../threadStore.ts` | `queued`, `queueHeld`, `queueError`, `cut`; `pendingRestored`; pending reconciled against the server queue. |
| `frontend/.../sendPlan.ts` | No "queue" outcome; `awaitThread` is the only thing the client withholds. |
| `frontend/.../useSendPipeline.ts` | Durable-before-POST; handles `queued`; treats an old backend's `deferred` as a failure. |
| `frontend/.../useThreadLog.ts` | The change stream; poll demoted to a 60s backstop. |
| `frontend/.../transcriptBlocks.ts` | Scope blocks from the log; cut from the server; `withStaleFailures` deleted. |
| `frontend/src/lib/chatSelection.ts` | Queue storage replaced by the outbox. |
| `frontend/src/lib/draftSync.ts` | Key discipline; server-owned clear; attachments. |

---

## 6. The wire

```
GET  /api/chat/threads/{id}/events?after=<cursor>
  → events, last_seq, active_run,
    queued[], queue_held, queue_error,      # the server's queue
    cleared_through, cut_mode               # the cut it is applying

GET  /api/chat/threads/{id}/stream?since=<version>
  → SSE. `event: changed` with a version; `: keepalive` otherwise.
    Carries no content. The client syncs.

POST /api/chat/threads/{id}/turns
  body adds: sent_at, draft_key
  → {turn_id} | {turn_id, injected, at_segment}
  | {turn_id, injected: false, queued: true, queue: {...}}

POST /api/chat/threads/{id}/inject
  body adds: sent_at, draft_key
  → adds {queued: true, queue: {...}} — TAKEN, do not re-send

POST /api/chat/threads/{id}/queued/{client_id}/cancel   → queue state
POST /api/chat/threads/{id}/queued/release              → queue state
POST /api/chat/threads/{id}/compact
  → {folded, session_reset, error, summary_chars}
```

**`deferred` is gone.** An older backend still answers it; the current client
treats it as a failure and keeps the text in the outbox, because a deferral
means the server took nothing.

---

## 7. Rules for anyone working here

- **A message is "sent" when the server says so.** The three acceptances are: a
  turn started for it, it was handed to the run in flight, or it was queued on
  the thread.
- **Never add a second owner of the same fact.** A local mirror is acceptable
  only as a cache the server overwrites wholesale, or as the outbox.
- **Never derive a storage key from a ref React assigns during render.** It holds
  the outgoing value through a switch. This destroyed the message queue on a
  thread switch, then the draft on a chat switch.
- **Never delete a record to clear it when ordering matters.** Write a tombstone.
- **A failed write is never swallowed**, and never with a comment saying it will
  re-sync later.
- **Draining, batching and ordering are the server's.** Writing any of them in
  the browser means a message is being sent from a browser again.
- **The sync endpoint must not read the log** to answer anything but events.
  `TestTheSyncEndpointDoesNotReparseTheLog` is the guard.
- **The change stream carries no content.** It is a trigger, not a source.

---

## 8. Tests

Backend, 1682 passing (4 known failures, §10):

| file | what it pins |
|---|---|
| `test_chat_queue.py` (18) | Storage, ordering, idempotency, batching, hold, cancel, corrupt file, "the queue is not in the transcript". |
| `test_chat_queue_drain.py` (13) | Drains with no client; waits for a run; Stop holds; a failed start parks with a reason; startup delivery; ordering; drain idempotency; archived chats. |
| `test_chat_queue_api.py` (10) | The same over HTTP: "queued" not "deferred", a second device sees it, the draft is cleared, unsend, release. |
| `test_chat_compact.py` (18) | A clear actually clears; a fold shrinks the prompt and keeps the tail; the transcript is untouched; a failed fold changes nothing and says so; legacy boundaries inert; the cut agrees on the wire. |
| `test_chat_stream.py` (9) | Every write that should bump the version does; a live draft does not; waiters wake; a client already behind does not wait. |

Frontend, 525 passing:
`transcriptBlocks.test.ts` gained "everything in the transcript comes from the
transcript"; `chatSelection.test.ts` was rewritten for the outbox;
`sendPlan.test.ts` for the plan that no longer withholds.

---

## 9. Deployment state at handoff

- `76f9c66` and `b58ed7f` committed.
- Backend: the queue, drafts and compaction work is **live**. `chat_events` and
  the `/stream` endpoint were **not yet live** at the time of writing — a chat
  turn was running, and `restart_backend.sh --deferred` does not park: it sleeps
  180s, hits the live-turn guard, exits, and leaves the backend on old code with
  the failure only in `/tmp/workshop-backend.log`. A watcher is running that
  restarts the moment turns clear. **Verify with
  `curl -s localhost:8000/chat/threads/nope/stream`** — `{"detail":"thread not
  found"}` means live, `{"detail":"Not Found"}` means the route is missing.
- Frontend: the built origin carries everything through `76f9c66`. The change
  stream (`useThreadLog`) needs a rebuild, which was **deliberately not run**: a
  third session was mid-refactor in `Transcript.tsx` and `grouping.ts` and the
  tree was red there. Building would put a half-finished transcript refactor on
  the operator's origin. Run `bash scripts/build_workshop_public.sh` once
  `npm run typecheck` is clean.

---

## 10. Open, known, and deliberately not done

- **Four backend tests fail** and are not from this work:
  `test_chat_cut_points`, `test_chat_message_ordering`, `test_chat_run_lifecycle`
  (×2). They monkeypatch `write_to_turn` with two-argument lambdas against a
  signature that now takes three. Verified independently by two sessions (a
  stash here, a detached worktree there).
- **The summary is not readable in the UI.** The divider says messages were
  folded into a summary; there is no way to read it. The transcript above the
  line is complete, so nothing is lost, but a viewer would be honest.
- **HTTP/1.1 connection budget.** A chat may now hold two streams (the turn's and
  the thread's). Fine over HTTP/2; several tabs on HTTP/1.1 could contend
  against the six-connection cap.
- **`_COMPACT_KEEP_TAIL` is 6, chosen not measured.** The tail is what the
  conversation is about; too small makes a compacted chat unable to continue.
- **Compaction is manual only.** Nothing folds on its own. Automatic folding was
  removed earlier the same day for summarizing chats nowhere near full, and it
  should stay removed until there is a real threshold to fold at.
- **The outbox retries only on demand.** A send whose request never landed shows
  "Not sent" with a Retry, and does not retry itself when connectivity returns.
  That is deliberate for now: an automatic retry sends a message the operator
  may have given up on, and there is no signal yet distinguishing the two.

---

## 11. Traps

Things that cost time here, so they cost less next time.

- **`get_thread()["messages"]` is the whole transcript.** The cut is a separate
  count and applying it is the caller's job. That is the shape of the bug in
  §4.2; check any new prompt-assembly path.
- **`--deferred` restarts are not reliable** when another session has a turn
  running. Check the process start time and probe a route you just added.
- **This tree usually has several sessions in it.** Before concluding a failing
  test is yours, check the file's mtime and stash-verify. Two of my three
  "someone else broke it" calls were right and one of the peer's was wrong, in
  both directions, within the same hour.
- **Owner scoping at startup.** Anything iterating threads outside a request has
  no owner pinned and will quietly see nothing.
- **The context meter is a second implementation of prompt assembly.** It was
  right while the real path was wrong, which is how the failure hid. Keep them
  reading the same inputs.
