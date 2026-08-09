# Chat baseline: research, audit, and rebuild plan (2026-08-09)

Operator directive: stop patching a mishmash; establish a solid, standard chat baseline.
Three parallel deep-dives fed this doc: external research on how mature chat systems are
built, a frontend audit, and a backend audit. This is the merged report and plan.

## Part 1 — External research: the standard baseline

Sources: Slack engineering (real-time messaging), Matrix client-server spec, Discord
snowflakes, OpenAI Assistants run model, Vercel AI SDK v5 (useChat, resumable streams),
assistant-ui runtimes, LibreChat, Ably (stream resumption), TanStack (chat virtualization),
CSS-Tricks (scroll pinning). Full citations at the bottom.

The twelve load-bearing decisions every solid chat system converges on:

1. **Append-only event log per chat, server-assigned monotonic `seq`.** Events, not just
   messages: message, run-started, run-finished, boundary, title-change. Never rewrite the
   log; never order by array position. (Matrix rooms, Slack per-channel logical clock.)
2. **Client-generated message ID as idempotency key.** Server dedupes on
   `(chat_id, client_id)`; retries re-send the same ID. (Discord `nonce`, Slack.)
3. **Optimistic send reconciled by client ID**, with explicit pending/sent/failed states.
   Never reconcile by tail index or content equality.
4. **The in-flight turn is a run object** with its own ID and status machine
   (`queued → in_progress → terminal`), separate from the message list. The chat record
   stores `active_run_id`. (OpenAI Runs, assistant-ui ThreadRuntime.)
5. **The server commits the final assistant message exactly once**, at run end, whether or
   not a client is watching. The client never persists streamed text.
6. **Resumable SSE**: chunks buffered per run keyed by offset; requests carry a cursor
   (`Last-Event-ID`); reconnect replays then tails; on mount, reattach if `active_run_id`.
7. **One sync endpoint**: `GET /chats/:id/events?after=seq` returns everything in order;
   client applies with dedupe-by-ID. A transcript assembled by merging multiple endpoints
   plus local state is the anti-pattern behind most homegrown chat bugs.
8. **One reducer-driven client store**, keyed by chat ID, messages normalized by ID,
   transcript as a derived view (committed + pending sends + live run). Pure, testable.
9. **One writer per conversation on the server** (per-chat asyncio queue). All mutations
   serialize through it; `seq` assignment and turn-queueing fall out for free. Optimistic
   version-checking is strictly worse for a single-process app.
10. **Compaction/clear are appended boundary events.** Context is a *view* over the log
    (summary + events after `boundary_seq`); the UI always shows full history. A message
    concurrent with compaction lands deterministically before or after the boundary.
11. **Plain (non-virtualized) list** into the thousands of messages; IntersectionObserver
    bottom-sentinel scroll pinning with user-scroll override and jump-to-latest.
    Virtualized chat (prepend stability, dynamic heights) is genuinely hard; don't buy it
    speculatively.
12. **Ruthlessly minimal features**: send/retry, streaming with stop + refresh-safe resume,
    queue-during-turn, named chats (list/create/switch/rename/delete), load-older
    pagination, compact/clear, in-log error surfacing, markdown, scroll pinning. Deliberately
    NO edit/delete of sent messages, no branching/regenerate, no presence/receipts/typing,
    no search initially (FTS over the log can come later without architectural impact).

## Part 2 — Audit: where we stand

### What is already right (keep)

Backend:
- **The detached-process turn engine** (`build_streaming.py`): disk journal as truth, FIFO
  stdin, tail-to-drive, offset-addressed SSE with `Last-Event-ID` resume,
  `reattach_running_turns()` on restart. This already matches decisions 5 and 6. It needs
  tests, not replacement.
- **Compaction/clear model**: append-only transcript + `compacted_through` boundary +
  summary, field-owned writes, fold-vs-clear checked. Matches decision 10 in spirit.
- `chat_store` discipline: append-only messages, `update_thread` field ownership,
  `LostUpdate` tripwire.

Frontend:
- `lib/chatSelection.ts` (pure `resolveScope`, one-shot URL intents) — the finished answer
  to "opens blank / opens the wrong chat".
- Pure tested modules: `segments.ts`, `turnBatching.ts`, `contextCut.ts`.
- `useLogScroll`, `useMessageQueue` (extracted, invariant-documented).
- `useTurnStream`'s hard-won rules (drop ≠ finish, `findLiveTurn`, read-first retire).
- Rendering leaves (`ChatLog`, `MessageList`, `LiveTurn`, `Transcript`), `Chats.tsx`.

### The structural gaps (why bugs keep recurring)

1. **No sequence numbers anywhere.** Ordering is array position in a whole-file-rewritten
   JSON document (`chat_store.py`). `compacted_through` is an index. Violates decision 1;
   this is the enabling condition for every lost-update class hit so far.
2. **The transcript has five client-side owners**: `messages`, `pendingSegments`,
   `sentDuringTurn`, `queue.items`, voice `settling` — concatenated by convention in
   `ChatLog.tsx`. Every historical reorder/eaten-message/double-bubble bug is a coherence
   failure between two of these five. Violates decisions 7 and 8.
3. **Reconciliation by tail index + content equality** (`Chat.tsx:819-840`,
   `useCompaction.ts:146`, queue drop-by-text). No client IDs. Violates decisions 2 and 3.
4. **No single writer.** Store safety rests on "no `await` between read and write" (a
   convention), a shrink-only tripwire, and one off-loop violator: `chat_naming` does full
   read-modify-write in a worker thread (`chat_naming.py:114-128`) and can silently clobber
   fields. Violates decision 9.
5. **Placeholder gap**: two `POST /turns` in the same loop tick can both see "no live turn"
   and start rival turns (`build_streaming.py:604` vs `:650`).
6. **Injection persists after FIFO delivery**; a failed `_file_during_turn` means the model
   read a message the transcript never recorded (`routes/chat.py:2047-2049`).
7. **Two completion paths** (`POST /messages` blocking vs `POST /turns` streaming) each
   with their own finalize pipeline — drift waiting to happen.
8. **Chat.tsx is a 1,360-line god component** (~54 hook calls, ~30 state atoms); the send
   path is a four-branch guard ladder plus a 250 ms timer race in the queue drain.
9. Timer-mediated races throughout (250 ms drain re-check, 2.5 s auto-name refetch,
   render-phase ref writes, suppressed effect deps) — each a patched incident.
10. Streaming engine has near-zero tests (journal tailing, finalize branches, SSE resume,
    reattach) despite being the headline bug area.

## Part 3 — Plan

### Phase 1 (implementing now — clear, standard, bounded)

1. **Client IDs end-to-end.** Frontend stamps `client_id` (crypto.randomUUID) on every
   send; backend stores it in `meta` and dedupes repeats within a thread; all optimistic
   reconciliation (injected-echo pop, failure marking, retry, queue drop, compact-bubble
   matching) moves from tail-index/content-equality to ID match. Stable render keys.
2. **Auto-namer back on the loop.** Model call in `to_thread`, store write on the event
   loop (the split compaction already uses). Removes the last off-loop writer.
3. **Live-turn latch.** Per-thread in-memory latch set before `start_turn` returns, closing
   the rival-turn window.
4. **Persist-then-inject.** File a mid-turn message in the store before writing it to the
   FIFO, so the transcript can never be behind what the model saw.
5. **Dead surface removal**: FocusedChat's redundant thread fetch, the dead
   `injectIntoTurn` client. Regression tests for 1-4.

### Phase 2 (proposed, needs operator sign-off — the real baseline core)

A. **Seq-numbered store**: per-thread append-only log with server-assigned `seq`
   (recommend: keep JSON-file-per-thread but split `meta.json` + `log.jsonl` append-only;
   sqlite is the alternative). `compacted_through` becomes a seq. Store API becomes
   `append / patch_message(seq) / set_meta` — whole-document write-back impossible by
   construction. One-time migration of existing threads.
B. **Single writer per thread**: per-thread asyncio queue through which every mutation
   flows.
C. **One events endpoint**: `GET /threads/{id}/events?after=seq` carrying messages, run
   markers, boundaries, title changes; stored log and live SSE share the seq space. Client
   stops merging three sources.
D. **Client reducer store** keyed by thread id (committed-by-seq + pendingSends-by-clientId
   + activeRun), transcript as a derived selector; Chat.tsx shrinks to composition
   (~2,000-line rewrite, keeping the leaves and pure modules).
E. **Collapse the two completion paths**: `POST /messages` becomes a wrapper that starts a
   turn and awaits finalize (affects the voice path — needs care).
F. **Streaming-engine test suite**: journal tailing, finalize branches, SSE resume, restart
   reattach, concurrent send+compact.

Explicitly NOT planned: virtualized list, message edit/delete, branching/regenerate,
presence, search (later, against the same log).

## Sources

Slack real-time messaging (slack.engineering) · Matrix client-server API ·
"Message order in Matrix" (artificialworlds.net) · Discord snowflake structure ·
OpenAI Assistants deep dive · Vercel AI SDK chatbot-resume-streams · Ably on AI stream
resumption · assistant-ui ThreadRuntime / ExternalStoreRuntime · LibreChat data flow ·
TanStack "Chat UIs Are Lists Until They Aren't" · CSS-Tricks pin-scrolling-to-bottom ·
Microsoft Agent Framework compaction · mem0 summarization guide.
