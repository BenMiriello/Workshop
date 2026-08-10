# Chat: what is left, and how each thing gets fixed (2026-08-10)

Written after four audits and four fix waves. Ordered by what it costs the operator.
Everything here was verified against current code, not inferred from commit messages.

## 0. Open right now, unresolved

### 0.1 A message renders above work that preceded it (REGRESSION, highest priority)

Reported twice in one session: "my message shows ABOVE the last system block".

Ruled out with evidence, do not re-litigate:
- Backend/bundle staleness. The running backend serves the stop and events routes (401 on
  real paths, 404 on bogus ones) and the built bundle contains the current client.
- `TURNS` pruning dropping a live run. `_prune_turns` only deletes turns with
  `finished_at` set.
- Client/server text-merge mismatch. `_seg_text` and `pushText` merge identically.

**Leading hypothesis, not yet proven.** `at_segment` is `len(turn.segments)`, a count of
TOP-LEVEL segments. A subagent's steps nest INSIDE one agent segment, so during a long
delegation the top-level count barely moves while the screen fills with rendered work. A
message typed ten minutes into a subagent run gets the index from before the delegation
started, and the client cuts there, placing it above everything the chip has accumulated.
This session's turns are subagent-heavy, which fits both the symptom and its
intermittency: it only shows up when a delegation dominates the turn.

**What would settle it:** on a live turn with a long-running subagent, sample
`len(get_turn(run_id).segments)` every 10s and compare against the number of rendered
blocks. If the count is flat while the screen grows, that is the mechanism.

**The fix, if confirmed.** A top-level index cannot express "inside the third step of the
open delegation". Record placement against something monotonic that counts everything
rendered: either the run's cumulative event ordinal (the SSE offset already used for
`Last-Event-ID`), or a `(segment_index, nested_step_index)` pair. The client cut then
becomes a lookup on the same basis instead of an array index. This is a schema change to
`meta.at_segment`, so it needs a fallback for messages already on disk.

### 0.2 The context meter fell from 44% to 8%

Verified: 79,964 tokens against a 1M window for `claude-opus-5` is 8.0%, so the current
reading is arithmetically right, the model never changed, and no compaction has run on
this thread (`boundary_seq` 0, no summary). So the measured count itself dropped, roughly
440k to 80k. I have not established why, and I gave one wrong explanation already
(a model switch); the next answer needs the per-turn measurements, not another guess.

Candidates to check, in order: a turn that ran on a fresh session sends far less than a
resumed one, so `context_tokens` legitimately collapses; the `/context` estimate path
substituting for the measurement; `_window_for`'s self-correction moving a tier.

## 1. Voice, which is the least honest surface left

1. **Interrupt is fire-and-forget and keeps billing.** `_interrupt` swallows every
   exception, `cancel_turn` calls `_finish_turn` unconditionally, and `_pump` drops the
   later `result` event for a turn already marked complete, so the continuation's tokens
   are spent and never reach the usage ledger. Fix: wait for the CLI's control response
   with a bounded timeout, say so honestly on timeout, and always let usage events
   through.
2. **Server TTS keeps synthesizing after stop.** The `/voice/tts` fetch carries no
   `signal` and `stop()` only pauses the audio element. Fix: one `AbortController` per
   speaker.
3. **Exiting voice leaves a resident process for 10 minutes.** `DELETE /voice/session/{id}`
   exists and has no caller. Fix: call it on exit with `keepalive`.
4. **A dead mic that says "Listening".** `armListen` sets the state before the early
   return, so with no STT provider the mic is open and nothing will ever transcribe. Fix:
   set an error and close the mic.
5. **Voice turns have no run object**, so `active_run` is null and the "a run in flight is
   always visible" rule is false for voice. Fix: wrap the voice turn in the same
   open/claim/close_run the text path uses.
6. Dead: `voiceSession.submitText`, unreferenced since typed text was routed away.

## 2. Silent staleness

7. **Poll failures are silent once a thread has content.** `useThreadLog` reports only when
   the transcript is empty, so a backend down for minutes leaves a quietly stale chat.
   Fix: count consecutive failures, expose `staleSince`, say so regardless of length.
8. **A queue only self-drains in the tab that typed it.** `bindKey` reads storage once on
   bind. Fix: a `storage` listener, or reuse the BroadcastChannel `draftSync` already has.

## 3. Small and true

9. `/clear` on a fully folded chat resets the agent's session and then reports "Nothing to
   clear". The response already carries `session_reset`; the client should read it.
10. Stop can leave a permanent "compacting" line under a message whose turn has ended.
11. Deleting a thread orphans its `ws-chat-hold:` key.
12. Auto-name refetches once on a 2.5s timer; a slower namer leaves the placeholder until
    a reload.
13. `/context`, `/cost`, `/model` are gated on `isBuild` on a premise that is now false:
    every thread has a session.
14. `POST /turns/{id}/inject` accepts `client_id` and drops it. The route is unused by the
    frontend, so this is latent.

## Sequencing

0.1 first: it is a regression, it is the one the operator keeps hitting, and it needs a
diagnosis before a fix. 0.2 next, same reason. Then voice 1-4 as one pass, because they
are one subsystem and two of them spend money. Then 7 and 8. Then the rest, which are
cheap and can go in one sweep.

## The rule this file exists to hold

Every one of these was found by asking "does this control do what it claims?", not "is
this code sound?". The second question passed a Stop button that never stopped anything.
Ask both, plus "what happens when it fails" and "is this guarantee enforced or merely
described".
