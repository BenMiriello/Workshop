# Voice mode: handoff, 2026-08-12 evening

Written at the operator's request, to continue in another chat. Everything here
is uncommitted working-tree state unless it says otherwise.

**Read `architecture-2026-08-12.md` first.** It is the design and the as-built
record. This file is what happened AFTER it: what shipped, what broke in the
operator's hands, and the one unresolved failure that matters most.

---

## 0. Start here: the live bug

**The operator dictated several messages, watched them appear in the chat,
switched voice off, and they were gone. They are not recoverable.**

Forensics, done at 13:15 PDT against the running store:

- Nine chat threads titled `Voice` were created in three bursts of three, at
  20:11:11, 20:12:18 and 20:13:30 UTC. Three per voice-on.
- **Every one of them has a 0-byte `log.jsonl`.** Nothing was ever persisted.
- No voice turn appears in the backend log for that window.

So the words never reached the server as a turn. What the operator saw was the
voice overlay rendering client-side state (`snapshot.pendingUser`,
`snapshot.lastUser`, `snapshot.sentences`), and `VoiceClient.stop()` does
`this.snap = EMPTY`, so switching voice off erased the only copy.

Two separate defects, both mine, both still open:

**0a. Voice showed the operator words it had not saved, then deleted them.**
This is the real bug. The voice overlay renders `snapshot.pendingUser`,
`snapshot.lastUser` and `snapshot.sentences` as chat bubbles at the foot of the
log, which is indistinguishable from a message. All three are client state.
`VoiceClient.stop()` does `this.snap = EMPTY`, so switching voice off erased the
only copy that ever existed.

Confirmed against the operator's own thread (`dbc089afae...`): between his
19:23 message and his 20:12 message there are seqs 241 to 254, and **every one
of them is a `run` event. Not a single user message was persisted in that
window.** So no spoken turn ever completed. He watched his words accumulate in
the overlay, no reply came, and toggling voice off wiped them.

Two things have to change, and the first is not cosmetic:

- A transcript that renders as a message MUST be persisted, or MUST be visibly
  provisional. It was neither.
- Work out why no endpoint fired. The turn-end ladder should have hit its 4s
  ceiling and started a turn. Candidates: `_typing` stuck true (the composer
  sets it and voice defers the endpoint while it is set), `_armed` false, or the
  socket connected but `_thread()`/`start_turn` threw. The backend log for that
  window shows one `connection closed`, no `connection open`, no
  `/voice/session` POST and no `WebSocket ... 403`, which is itself odd and is
  the thread to pull.

**0b. The nine empty "Voice" threads were MY TESTS, not a product bug.**

`tests/test_voice_stream_route.py` stubs `_user` to return owner id 6, which on
this box is the operator's REAL account, and it did not redirect the runtime
root. Three of its tests cause a turn, so every run wrote three real chats into
his real chat list. Three runs, nine threads, in bursts matching the test runs
exactly. They are indistinguishable from litter a live socket would leave, which
is most of what made this report so hard to read.

Fixed: the fixture now does `monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)`
before anything else. Verified by running the file and counting the operator's
chat directory before and after: delta 0.

**A test that authenticates as a real owner must never share that owner's
storage.** That rule is the durable lesson here, and it is not voice-specific.

---

## 1. What this session did

Three phases, in order.

1. **Research and design.** Audited the whole voice path, researched how
   production stacks solve it, and wrote `architecture-2026-08-12.md`: the three
   structural choices that generate the bugs, every reported symptom traced to
   one of them, and the replacement design.
2. **Built it.** Not a patch pass. The conversation moved to the server as a
   pure reducer; the client became a device. Deleted `voiceSession.ts` (1600
   lines), `voice.ts` (1126), `voiceTurnAction.ts`, `useReplySpeaker.ts` (which
   had no callers at all), and the per-turn REST surface.
3. **Fixed what the operator hit.** It did not connect at all on first use; then
   four UI complaints; then the data loss above.

---

## 2. The architecture, in one paragraph

Audio is ONE continuous timestamped stream the server owns. A segment is a pair
of sample offsets into a ring buffer, so cutting is arithmetic and lossless.
`services/voice/machine.py` is a pure reducer holding all the policy: no timers,
no I/O, one `_abandon`. `runtime.py` is the impure half (perception, ASR,
generation, speech out) and holds no policy. The client captures PCM, plays PCM,
and reports what was actually heard. That last part matters: only the browser
knows when a sample left the speaker, and whether the operator's next words are
a continuation or an interruption turns on it.

### Files

| file | holds |
|---|---|
| `backend/services/voice/timeline.py` | sample-indexed ring buffer (`Span`, `read`, `write_at`, `tail`) |
| `backend/services/voice/machine.py` | **all policy**, pure reducer |
| `backend/services/voice/detectors.py` | `SpeechDetector`, `TurnEndDetector` (ONNX or local fallback) |
| `backend/services/voice/clauses.py` | clause splitting, `clean_for_speech` |
| `backend/services/voice/runtime.py` | perception loop, ASR, generation, speech out |
| `backend/services/voice/protocol.py` | 8-byte frame header, cursor unwrapping |
| `backend/routes/voice_stream.py` | `/voice/stream`, auth + Origin check |
| `backend/workshop_public_app.py` | the edge's WebSocket proxy |
| `frontend/public/voice-*-worklet.js` | capture (resample to 16k, 20ms frames), playback (jitter buffer) |
| `frontend/src/lib/voice/` | `capture`, `playback`, `transport`, `client`, `protocol`, `modes`, `errors` |

### The one rule that survives every rewrite

**The line between a continuation and an interruption is whether anything was
HEARD, not how much time passed.** You cannot be reacting to a sentence that has
not made a sound, so talking over a reply that has not been heard means the
endpoint fired early: merge. Talking over one you heard means you are answering
it: interrupt.

---

## 3. Bugs found and fixed, in the order they were found

Each of these was real, and several were mine.

1. **Out-of-range `ClauseHeard` inflated the heard count**, which decides
   continuation vs interruption. Caught by the reducer's own tests.
2. **Mutable class attribute `_worker_turns`** shared across every session on the
   box.
3. **The decision tick was driven by frame arrival.** Looked equivalent at
   50fps; with a stalled or throttled client the endpoint was never re-evaluated
   and the turn hung with nothing to un-hang it. Now a 40ms clock, independent
   of transport.
4. **The energy VAD calibrated its noise floor on the first 600ms** and went
   permanently deaf if the operator was already talking. Same class of bug as
   the old client's 1200ms calibration pass. The floor now starts at zero and
   rises slowly: the recoverable error is "heard something it shouldn't", not
   "cannot hear you".
5. **A vacuous test of mine passed for the wrong reason** (connecting to a path
   that did not exist; Starlette 403s unmatched WebSocket paths exactly like a
   rejected one).
6. **The public edge could not carry a WebSocket at all.** httpx cannot forward
   an Upgrade. Voice would have worked on the Vite origin and failed on the
   built one the operator uses. Vite also needed `ws: true`.
7. **THE CONNECT BUG.** The Origin check compared the browser's `Origin` against
   the backend's own `Host`, which behind the edge is `127.0.0.1:8000`. Every
   genuine connection was refused. The backend log was solid
   `WebSocket /voice/stream 403` on a backoff ladder, which is exactly a spinner
   that never resolves. Fixed with `x-forwarded-host` plus a same-origin check
   at the edge, where the browser's socket actually terminates. Vite got
   `xfwd: true` for the same reason.
8. **A throw in `VoiceClient.start()` left the spinner turning forever**, because
   state only leaves `connecting` when the server speaks and the caller does not
   await. Now surfaces an error and returns to idle.
9. **Repeated socket failures were silent.** Now say so after three attempts.
10. **The client was rebuilt when the thread bound**, so a new voice chat
    restarted itself the moment the server told it which chat it was. That is
    why `/voice/session` was posted over and over. Fixed with `setBinding`.
11. **Nothing released the resident worker** after the lifecycle moved to the
    socket. Now released on a deliberate close, not on a network blip.

### UI fixes from the operator's four complaints

- Paperclip moved left of the whole voice group; the cluster and the toggle are
  contiguous.
- Hang-up icon gone. One glyph both ways, pressed state carries it.
- **Height jump fixed.** `.chat-voice-cluster` was its children's 2.1rem plus its
  own padding and border, so it was taller than every other control and grew the
  action row, moving everything above and below the composer.
- Context meter no longer hidden while voice is on.

---

## 4. Settings: eight knobs became three

Dropped because they tuned mechanisms that no longer exist:
`endpoint_silence_ms`, `eager_endpoint`, `eager_silence_ms` (all tried to answer
"have they finished" with a stopwatch, which no value can do) and
`barge_sensitivity`, which was labelled as an interruption preference and was
actually the master gate on whether you were heard at all.

Kept: input mode, output mode, voice, speed, and ONE `reply_pace`
(snappy / natural / patient) that biases the turn-end model.

**A knob whose correct value the system can measure is a bug wearing a control.**

---

## 5. The perception models are NOT installed

`onnxruntime` 1.27 is present and both models are tiny, but this process cannot
fetch them (shell egress is blocked by policy, correctly). Drop these in and
restart; they activate by themselves:

```
.runtime/voice/models/silero_vad.onnx      ~2 MB
.runtime/voice/models/smart-turn-v3.onnx   ~8 MB   (pipecat-ai/smart-turn-v3, BSD-2)
```

`GET /api/voice/health` reports which tier is live and the settings sheet shows
it, so the surface never implies accuracy the box does not have.

The fallbacks are real, not placeholders. Turn detection is a silence ladder
crossed with linguistic completeness: a finished sentence endpoints at 380ms, an
unpunctuated one at 900ms, one ending mid-clause waits 2200ms, and 4s ends
anything. That ladder is most of what smart-turn buys.

---

## 6. State, and how to verify

```
cd backend && .venv/bin/python -m pytest tests/ -q -k "voice or edge"   # 130 pass
cd frontend && npm test
npm run typecheck                                                       # clean
bash scripts/check_design.sh                                            # clean
bash scripts/build_workshop_public.sh && bash scripts/ui_sync.sh
```

Everything is **uncommitted**. 19 untracked files, plus modifications across
`backend/routes/voice*.py`, `backend/workshop_public_app.py`,
`frontend/src/lib/voice/*`, `useVoiceSession.ts`, `ChatComposer.tsx`,
`VoiceSettings.tsx`, `vite.config.ts`, and one sizing rule in
`packages/ui/src/styles/components.css`.

### Things that are NOT this work

- Five backend failures in the full run (`test_chat_cut_points`,
  `test_chat_message_ordering`, two in `test_chat_run_lifecycle`,
  `test_paths_are_portable`) are the pre-existing attachments WIP.
- One frontend failure, `transcriptBlocks.test.ts` "draws nothing for a boundary
  left by the removed folding path", belongs to the session doing the folding
  removal. `transcriptBlocks.ts` was edited at 12:15 today; the test is older. I
  flagged it to them and did not touch either file.
- **Another session is editing frontend source concurrently.** Every build goes
  stale within seconds. I verified my own changes are in the served bundle by
  grepping `frontend/dist/assets/*.js` for `Turn voice off`, `Voice could not
  start`, `Voice can't reach the server` and `voice-capture-worklet`, rather
  than trusting the stamp. Whoever finishes last should run the final build.

---

## 7. What to do next, in order

1. **Fix the data loss (section 0).** Nothing else matters until a dictated
   message cannot vanish. Two parts: persist the utterance when it becomes a
   turn, and stop creating a chat per connection. Start at `/voice/session`.
2. **Work out why the socket appears not to have connected** in the 13:11 to
   13:13 window while threads were still created. That answer probably explains
   0b completely.
3. **Then use it**, and watch Voice settings > Diagnostics: `Connection`,
   `Microphone`, `Echo cancellation`.
4. Drop in the two ONNX files.
5. Streaming ASR (Moonshine or Parakeet) behind `_transcribe`, which is already
   the only seam it needs.

---

## 8. The honest assessment

The architecture is sound and I stand behind it: the reducer is testable, the
timeline removes a whole class of bug by construction, and the client is now
small enough to port to Expo. 130 tests pass.

But it has been shipped three times today and the operator has had a bad
experience three times: it did not connect at all, then the UI was wrong, then
it ate his words. Every one of those was in a path no test executed, and the
pattern is the same one this rewrite was supposed to end. The tests cover the
machine; they do not cover the machine being wired to a real browser through a
real proxy. Until something exercises that path end to end, the next session
should assume it is broken in a way the suite cannot see, and check the backend
log against the operator's account before believing anything works.
