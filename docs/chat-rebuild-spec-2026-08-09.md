# Chat rebuild: the target design (2026-08-09)

Companion to `chat-baseline-2026-08-09.md` (research + audit). That doc says what is
wrong and why. This one is the spec three implementation passes build against. Operator
approved a full redesign, not a patch series.

## The one idea

**A thread is an append-only, sequence-numbered event log. Everything else is a view of
it.** The context sent to the model is a view. The transcript on screen is a view. A live
turn is not in the log at all until it finishes.

Every recurring bug traced back to violating one of those sentences.

## Storage

Per thread, a directory `.runtime/users/<owner_id>/chat/<thread_id>/`:

- `log.jsonl` — append-only. One event per line, never rewritten, never spliced.
- `log.1.jsonl` — the same log, colder. Added 2026-08-10: once `log.jsonl` passes
  `chat_log.ROTATE_BYTES` its head is appended here verbatim and the live file keeps the
  tail. No event is dropped, no seq is renumbered, and `read_events` reads the two as one
  log, so this is invisible to every reader including one holding an old cursor. The
  per-event rule above is unchanged: a written line is never edited, only relocated.
- `meta.json` — small mutable record, rewritten whole (it is small, and one writer owns it).

### Event shape

```json
{"seq": 41, "id": "01J...", "ts": "2026-08-09T19:52:41.149Z", "kind": "message",
 "role": "user", "content": "...", "client_id": "uuid-from-browser", "meta": {}}
```

`seq` is 1-based, assigned by the writer, monotonic per thread, never reused. `id` is a
ULID. Kinds:

| kind | payload | meaning |
|---|---|---|
| `message` | `role`, `content`, `client_id?`, `meta` | a user or assistant message |
| `run` | `run_id`, `state` (`started`/`finished`/`lost`), `meta` | a turn's lifecycle marker |
| `boundary` | `mode` (`compact`/`clear`), `summary?` | context cut at this point |
| `title` | `title`, `source` (`auto`/`manual`) | the thread was named |

### meta.json

`id, context_key, scope, mode, participants, model, title, created_at, updated_at,
last_seq, active_run_id, boundary_seq, summary, claude_session, agent_sessions,
card_states`.

`boundary_seq` replaces `compacted_through` (an index into an array that could shift).

## The single writer

One `asyncio.Lock` per thread id, held across the whole read-modify-write of `meta.json`
and the append to `log.jsonl`. Every mutation goes through the store; there is no other
way to write a thread. Concretely the store exposes only:

- `append(thread_id, event) -> seq` (assigns seq, appends, updates `last_seq`)
- `append_message(thread_id, role, content, client_id=None, meta=None)` — idempotent on
  `client_id`: if that client_id is already in the log, return the existing event and
  append nothing
- `patch_meta(thread_id, **fields)` — only the named fields, never a whole-document write
- `read_events(thread_id, after=0, limit=None)` — the log, folded, from a cursor
- `read_thread(thread_id)` — meta + a bounded tail of events, for the initial render

Whole-document write-back is not exposed, so it cannot happen. Any caller that wants to
mutate from a worker thread must go through `asyncio.run_coroutine_threadsafe`; the store
asserts it is on the loop.

This deletes, by construction: the `LostUpdate` tripwire, the "no await between read and
write" convention, the auto-namer's off-loop clobber, and the rival-turn window.

## The run is not a message

While a turn is in flight there is **no assistant placeholder in the log.** The log gets
one `run: started` marker; `meta.active_run_id` points at the run. Live output lives where
it already lives and works: the turn's disk journal under `.runtime/turns/<run_id>/`.

On finalize, the writer appends exactly one `message` event (role assistant, with the
folded segments in `meta`) plus a `run: finished` marker, then clears `active_run_id`.
Commit happens whether or not a client is watching.

Consequences, all of them good:

- No 0.4s store writes during a turn. The journal is the buffer; the log is the record.
- Reattachment is `meta.active_run_id`, one field read. No backward scan for a streaming
  flag, no self-heal of orphaned placeholders.
- A crashed turn leaves `active_run_id` set with a dead journal; startup reconciles by
  appending `run: lost` and a message saying so. Same reconcile path as today, simpler
  trigger.
- Two sends in the same tick cannot both start a run: the second takes the lock, sees
  `active_run_id`, and joins it.

## Mid-turn messages

Persist first, then deliver. `append_message` returns a seq; only then are the bytes
written to the turn's stdin FIFO. If delivery fails, the message is still in the log and
the caller starts a new run for it. The transcript can never be behind what the model saw.

The message renders below the running turn, per the existing append-only display rule in
`frontend/CLAUDE.md`. That rule does not change; the log now enforces it instead of the
renderer.

## One sync endpoint

`GET /chat/threads/{id}/events?after=<seq>` returns

```json
{"events": [...], "last_seq": 57, "active_run": {"run_id": "...", "started_seq": 55} }
```

The client calls it on mount, on focus, on reconnect, and after any write. It never
assembles a transcript from more than this plus the live SSE for the active run. The SSE
stream keeps its current offset-addressed resume (`Last-Event-ID`), which is already
correct.

`POST /chat/threads/{id}/messages` (the blocking path used by voice and scripts) becomes a
thin wrapper: start a run, await its finalize, return the committed message. One finalize
pipeline, not two.

## Client state

One reducer store per thread id:

```ts
{ meta, byId: Record<EventId, Event>, order: Seq[], cursor: Seq,
  pending: Record<ClientId, PendingMessage>, activeRun: {...} | null }
```

Actions: `eventsReceived`, `sendStarted`, `sendAcked`, `sendFailed`, `runDelta`,
`runCommitted`. The transcript is a derived selector: committed events in seq order, then
the active run's live view, then pending sends. Reconciliation is by `client_id`, never by
tail index or content equality.

`Chat.tsx` becomes composition over that store. The pure modules that already exist
(`segments`, `turnBatching`, `contextCut`, `chatSelection`) are kept as-is; the scroll and
queue hooks are kept; `sentDuringTurn` and the streaming-placeholder handling are deleted
because the log now expresses what they were simulating.

## Migration

One-shot, at startup, idempotent: for each `<thread_id>.json`, create the directory, write
`meta.json`, and replay `messages[]` into `log.jsonl` with seqs 1..N, mapping
`compacted_through` to `boundary_seq`. Move the old file to `<thread_id>.json.migrated`.
Never destructive; a failed migration leaves the original in place and logs loudly.

## Out of scope, deliberately

Message edit/delete, branching/regenerate, virtualized list, search, presence, read
receipts, multi-device conflict resolution. The log makes each of these addable later
without another rebuild, which is the point of doing it this way.

## Passes

- **A — store.** `chat_log.py` (log + meta + writer + client_id idempotency), migration,
  `chat_store` re-implemented on top with its current call signatures preserved so every
  caller keeps working. Full unit tests. Nothing else changes.
- **B — turn + API.** Run object out of the log, journal as live buffer, events endpoint,
  persist-before-inject, blocking path folded into the run pipeline, startup reconcile.
  Tests for reconnect-mid-stream, concurrent send+compact, crash mid-turn, same-tick rivals.
- **C — client.** Reducer store, events cursor, ID-based reconciliation, `Chat.tsx` reduced
  to composition, dead surface removed.
