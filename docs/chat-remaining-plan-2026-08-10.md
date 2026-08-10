# Chat: what is left, and how each thing gets fixed (2026-08-10)

Written after four audits and four fix waves, then revised once the two open
diagnoses were settled against real data from the operator's own threads. Ordered by
what it costs him. Everything here was verified in code or in `.runtime`, not inferred
from commit messages.

## Settled since the first draft

### Context meter fell from 44% to 8% — FIXED (`e3d6710`)

**Mechanism, proven.** `context_tokens` is taken from the usage block of the latest
assistant call (`build_streaming._drive_turn`, the `_multi` branch). A subagent's
assistant messages arrive on the same stream carrying `parent_tool_use_id`, and there was
no parent check, so the last delegate to speak overwrote the thread's reading. A delegate
runs on its own fresh context: roughly 80k against this thread's 440k. 440k/1M is 44%,
80k/1M is 8%. Nothing had been compacted (`boundary_seq` 0, no summary) and the model
never changed.

**Fix.** Only the turn's own calls set the reading. Spend is the opposite case and still
counts every call, delegates included, because a delegate's tokens are really spent.
Pinned by `backend/tests/test_context_measurement.py` (4 tests).

### A message renders above work that preceded it — MECHANISM PROVEN, FIX DESIGNED

**Evidence**, from this conversation's own log (`d899901fe4fe`):

| reply | top-level segments | nested steps | biggest single chip |
|---|---|---|---|
| seq32 | 128 | 672 | 175 |
| seq39 | 84 | 90 | 37 |

and the messages that interrupted them:

| message | at_segment | reply size | rendered BELOW the message |
|---|---|---|---|
| seq27 | 7 | 135 | 128 top-level + 188 nested |
| seq36 | 5 | 84 | 79 top-level + 53 nested |
| seq37 | 42 | 84 | 42 top-level + 53 nested |
| seq38 | 42 | 84 | 42 top-level + 53 nested |

**Mechanism.** `at_segment` is `len(turn.segments)`, a count of TOP-LEVEL segments. A
subagent's steps nest inside a single `agent` segment, so while a delegation runs the
top-level count does not move at all. seq37 and seq38 were sent at different times and
both recorded 42, because the counter was frozen for the whole delegation. Everything the
chip accumulates renders as one unit that cannot be cut, so a message is either entirely
above all of it or entirely below, and the growing chip keeps rearranging itself around a
message that is already placed. That is why it is intermittent: it only bites when a
delegation dominates the turn, which is exactly what this session's turns look like.

**Ruled out with evidence, do not re-spend the time:** backend/bundle staleness (the
running process serves the stop and events routes, 401 on real paths vs 404 on bogus, and
the built bundle contains the current client); `TURNS` pruning dropping a live run
(`_prune_turns` only deletes turns with `finished_at` set); a client/server text-merge
mismatch (`_seg_text` and `pushText` merge identically).

**The fix.** Count rendered UNITS, not top-level segments: a unit is a top-level segment
or a nested step, so the counter advances while a delegation runs. Server records the unit
count; the client cuts on the same basis, which means a cut can land inside an agent chip
and split it into the steps before and the steps after. Record it under a new key
(`at_unit`) and keep reading `at_segment` for messages already on disk, so the change is
additive and old transcripts keep their current placement rather than silently shifting.

Both halves are pure functions and get tests: the server's unit counter against a turn
with nested steps, and the client's splitter against a cut that falls inside a chip.

## Voice, the least honest surface left

1. **Interrupt is fire-and-forget and keeps billing.** `_interrupt` swallows every
   exception, `cancel_turn` calls `_finish_turn` unconditionally, and `_pump` drops the
   later `result` event for a turn already marked complete, so the continuation's tokens
   are spent and never reach the ledger. Fix: await the CLI's control response with a
   bounded timeout, say so plainly on timeout instead of pretending, and always let usage
   events through the pump.
2. **Server TTS keeps synthesizing after stop.** The `/voice/tts` fetch carries no
   `signal` and `stop()` only pauses the audio element. Fix: one `AbortController` per
   speaker, aborted in `stop()`.
3. **Exiting voice leaves a resident process for 10 minutes.** `DELETE /voice/session/{id}`
   exists and has no caller anywhere. Fix: call it on exit with `keepalive: true`.
4. **A dead mic that says "Listening".** `armListen` sets the state before its early
   returns, so with no STT provider the mic is open and nothing will ever transcribe. Fix:
   set an error and close the mic instead of claiming to listen.
5. **Voice turns have no run object**, so `active_run` is null and the "a run in flight is
   always visible" rule is false for voice. Fix: wrap a voice turn in the same
   open/claim/close_run the text path uses.
6. Dead: `voiceSession.submitText`, unreferenced since typed text was routed away.

## Silent staleness

7. **A failed poll says nothing once a thread has content.** `useThreadLog` reports only
   when the transcript is empty, so a backend down for minutes leaves a quietly stale
   chat. Fix: count consecutive failures, expose `staleSince`, and say so regardless of
   how much is on screen.
8. **A queue only self-drains in the tab that typed it.** `bindKey` reads storage once on
   bind. Fix: a `storage` listener, or reuse the BroadcastChannel `draftSync` already has.

## Small and true

9. `/clear` on a fully folded chat resets the agent's session and then reports "Nothing to
   clear". The response already carries `session_reset`; the client should read it.
10. Stop can leave a permanent "compacting" line under a message whose turn has ended.
11. Deleting a thread orphans its `ws-chat-hold:` key.
12. Auto-name refetches once on a 2.5s timer; a slower namer leaves the placeholder until
    a reload.
13. `/context`, `/cost`, `/model` are gated on `isBuild` on a premise that is now false:
    every thread has a session.
14. `POST /turns/{id}/inject` accepts `client_id` and drops it. Unused by the frontend, so
    latent.

## Sequencing

The ordering fix first: it is the one he keeps hitting, the mechanism is now proven, and
the design is settled. Then voice 1-4 as one pass, since they are one subsystem and two of
them spend money. Then 7 and 8. Then the rest in one sweep.

## The rule this file exists to hold

Every item here was found by asking "does this control do what it claims?", not "is this
code sound?". The second question passed a Stop button that never stopped anything, and a
context meter that was measuring a different conversation. Ask both, plus "what happens
when it fails" and "is this guarantee enforced or merely described".
