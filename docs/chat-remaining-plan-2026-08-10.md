# Chat: what was wrong, and what was done about it (2026-08-10)

Written after four audits and four fix waves, revised once the open diagnoses
were settled against real data from the operator's own threads, and revised again
as the plan was implemented. Everything here was verified in code or in
`.runtime`, not inferred from commit messages.

Every item below is now **done**. What is left is listed at the bottom, and it is
two decisions for the operator rather than work.

## The three that cost him the most

### A message rendered above work that preceded it — FIXED (`9dc7d9e`)

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

**Mechanism.** `at_segment` was `len(turn.segments)`, a count of TOP-LEVEL
segments. A subagent's steps nest inside a single `agent` segment, so while a
delegation ran the top-level count did not move at all. seq37 and seq38 were sent
at different times and both recorded 42, because the counter was frozen for the
whole delegation.

**A second mechanism, found while fixing the first.** Streamed text merges into
the trailing text segment. So "A", a message, then "B" is ONE segment `"AB"` by
the time the reply is committed, and the message renders below text written after
it — the same misplacement arriving by a different route. Counting alone would
have fixed half the bug and left the operator reporting it again.

**Fix.** Cuts are counted in rendered UNITS (a top-level segment OR a nested
step, `build_streaming.unit_count` / `transcriptUnits.ts`), so the coordinate
advances with every step a delegate takes and a cut that lands inside a chip
divides it. A mid-turn message also SEALS the open text on both sides — the
server for the turn it drives, each tab for what it renders — so nothing written
afterwards can merge back above it. Recorded under a new key `at_unit` with
`at_segment` still written, so transcripts already on disk keep their placement.

**Ruled out with evidence, do not re-spend the time:** backend/bundle staleness
(the running process served the stop and events routes, 401 on real paths vs 404
on bogus, and the built bundle contained the current client); `TURNS` pruning
dropping a live run (`_prune_turns` only deletes turns with `finished_at` set); a
client/server text-merge mismatch (`_seg_text` and `pushText` merge identically —
they do, which is why BOTH needed sealing).

### `/compact` swallowed a message whole — FIXED (`9dc7d9e`)

Reported by the operator as "I sent the slash compact message then sent that last
message ... and I had to send it again". From the log:

| seq | time | what |
|---|---|---|
| 52 | 06:28:19 | `/compact` opens turn `02e6e390` |
| 53 | 06:29:00 | his message filed with `during_run=02e6e390`, `at_segment=0` |
| 54 | 06:30:23 | that turn replies "Compacted." and exits |
| 57 | 06:51:41 | he resends the same text, 22 minutes later |

**Mechanism.** The message was not queued and not lost in storage. It was
INJECTED INTO the compact turn, and a command turn never reads its input: it
folds and exits. It was handed to the one turn in the system guaranteed to ignore
it, and when that turn ended nothing put it back.

**Fix.** A turn that reads no input is marked at claim time
(`Turn.accepts_input`), `inject` refuses it, and the send path queues instead, so
the message goes out as its own turn the moment the command finishes. The server
no longer RECORDS a message it has not accepted: one in the log that nothing will
answer is worse than one the client still holds.

### The context meter fell from 44% to 8% — FIXED (`e3d6710`)

`context_tokens` was taken from the usage of the latest assistant call, and a
subagent's messages arrive on the same stream carrying `parent_tool_use_id` with
no parent check, so the last delegate to speak overwrote the thread's reading. A
delegate runs on its own fresh context: ~80k against this thread's ~440k. 440k/1M
is 44%, 80k/1M is 8%. Nothing had been compacted (`boundary_seq` 0, no summary)
and the model never changed. Only the turn's own calls set the reading now; spend
still counts every call, because a delegate's tokens are really spent.

## Voice — FIXED (`dc603a8`)

1. **Interrupt is answered, not assumed.** `_interrupt` wrote a control request
   and swallowed every exception; a pipe write succeeds whether or not anything
   acts on it. It now waits for the CLI's control response and reports what
   happened.
2. **An interrupted generation is still billed.** The pump dropped every event
   for a turn already complete, and a cancelled turn is closed immediately while
   the `result` carrying usage lands after — so every barge-in's tokens were
   spent and recorded nowhere. That also meant no control response could ever be
   matched, which is why (1) could not work until this was fixed.
3. **Stop stops the synthesis**, not just the playback: an `AbortController` per
   speaker, aborted in `stop()`.
4. **Exiting voice releases the worker.** `DELETE /voice/session/{id}` had no
   caller anywhere; a process sat resident for ten minutes waiting on the reaper.
5. **A dead mic no longer says "Listening".** With no usable STT the mic closes
   and the reason is shown.
6. **A voice turn is a visible run.** It claims the thread, as a run that takes
   no input, so typing during a spoken reply queues instead of opening a rival
   turn on the same session.
7. Dead `voiceSession.submitText` deleted.

## Silence — FIXED (`dc603a8`)

8. **A failed poll speaks however much is on screen.** It used to report only
   when the transcript was empty, so a full chat quietly minutes behind was the
   one case that said nothing. Three consecutive failures, with how long it has
   been.
9. **A queue drains in whichever tab notices the turn end.** Storage was read
   once at bind; a `storage` listener adopts another tab's write.

## Small and true — FIXED (`dc603a8`)

10. `/clear` on a fully folded chat no longer reports "Nothing to clear" for a
    session it has just reset (it reads `session_reset`, not only `cleared`).
11. A compact held by Stop stops pulsing a promise it will not keep.
12. Deleting a thread forgets its queue AND its `ws-chat-hold:` key, whichever
    thread it was.
13. The auto-namer looks four times with backoff instead of giving up 2.5s in.
14. `/context`, `/cost`, `/model` work in any chat: the premise that only build
    chats have sessions stopped being true and the gate stayed.
15. `POST /turns/{id}/inject` carries the `client_id` it was accepting and
    dropping.

## What is actually left

Neither is work; both are decisions.

- **The autonomy brake**, engaged since 2026-07-18 on a reason that no longer
  holds. Releasing it is the operator's call.
- **Whether `/status`, `/agents`, `/mcp`, `/memory`, `/init`, `/review` should
  follow `/context` out of the build-only gate.** They are about a project's
  working tree rather than about the conversation, so they were left gated. That
  is a judgement, not a finding.

## The rule this file exists to hold

Every item here was found by asking "does this control do what it claims?", not
"is this code sound?". The second question passed a Stop button that never
stopped anything, a context meter measuring a different conversation, and a
`/compact` that ate a message. Ask both, plus "what happens when it fails" and
"is this guarantee enforced or merely described".
