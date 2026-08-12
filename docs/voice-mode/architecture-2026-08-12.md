# Voice mode: the system, designed from the ground up

Status: **built**, 2026-08-12. Supersedes the architecture in `design.md`
(2026-07-11) and the incremental fixes recorded in
`../voice-mode-handoff-2026-08-12.md`.

Sections 1 to 9 are the design. **Section 14 is what actually shipped**, which
is phases 1 to 5 and 7's contract; read it before changing anything, because two
things landed differently from the plan and one open question was answered.

This document exists because voice mode has been fixed six times and is still
hard to use. Each round found a real bug and fixed it, and the next round found
another one in the same place. That pattern does not mean the fixes were wrong.
It means the thing being fixed cannot be made reliable by fixing it, because the
failures are not independent: they are the same three structural choices
producing a new symptom every time the surface moves.

So this is not a bug list with patches. Section 2 names the three choices,
section 3 traces every reported symptom back to one of them, and sections 4 to 9
are the replacement design. Section 10 is the build order, in independently
shippable pieces.

---

## 1. What we are building

A spoken conversation with the Workshop chat that:

- does not cut you off mid-thought, and does not make you wait when you are done
- lets you talk over it, and stops when you do
- never sends you words you did not say
- never hears itself
- starts answering inside a second
- works the same in a desktop browser, in mobile Safari, and later in the Expo
  app, because those three run the same system rather than three ports of it
- degrades honestly. When a lane is unavailable it says which one and keeps the
  rest working

The measurable targets:

| | now (measured from the code path) | target |
|---|---|---|
| last word to first spoken sample | ~2.5 to 3.5s | ≤ 900ms |
| audio lost at a mid-utterance pause | 10 to 80ms per cut, up to 3 cuts per utterance | 0, by construction |
| one thought split into N messages | routine at a 700ms pause | never below a 4s pause |
| assistant audio transcribed as the user | prevented by a compensating subsystem | structurally impossible |
| lines of platform-specific turn logic | ~1600, browser only | ~300, shared contract |

---

## 2. The three choices that produce the bugs

### 2.1 Audio is cut into files by stopping the recorder

Capture is `MediaRecorder` → `stop()` → hand the container to Whisper →
`start()` a new one. `MicStream.cutSegment()` is that pair, and it runs at every
eager pause, every endpoint, every barge, and at `closeCleanWindow`.

Four consequences, none of which is a bug in the code as written:

1. **There is a gap at every cut.** `stopRecording()` awaits the `onstop` event,
   and only then constructs and starts a new `MediaRecorder`. Neither recorder is
   capturing across that boundary. This is the residue of "it drops the middle of
   what I say": the last round of fixes shrank the deaf window from *the whole
   reply* down to *a few tens of milliseconds at each pause*, which is a large
   improvement and is still a real hole, positioned exactly where the interesting
   words are (a pause is where people put "and also...").

2. **Discarding is the only way to un-hear something.** `discardSegment()`
   throws away a buffer wholesale, so `finishSpeaking` normal completion drops
   whatever the user said in the moment the reply ended.

3. **Preview transcription is quadratic.** `previewTick` runs every 900ms over
   the segment *so far*, so a 10 second utterance decodes 1s + 2s + ... + 10s ≈
   55 seconds of audio to display a caption, then the endpoint decodes all 10
   seconds again at full quality. On CPU that is seconds of work per utterance,
   competing with Kokoro for the same cores, at the exact moment latency shows.

4. **There is no clock.** A segment is "the current one". There is no way to ask
   what was said between two instants, so every decision is taken against
   `performance.now()` and a mutable flag. That is why the session needs
   `capturing`, `endpointing`, `spokeWhileThinking`, `held`, `resumedSinceCut`,
   `previewBusy`, `micOff`, `micSuspended`, `eagerFires`, `reclaims`,
   `_interrupted`, and an `epoch` counter to make stale continuations no-ops.

Also: this whole path is the one that does not survive mobile. Safari has no
webm, produces fragmented mp4, and repeatedly stopping and starting a
MediaRecorder there is the least reliable thing in the browser audio surface.

### 2.2 The end of a turn is guessed from a timer

`endpoint_silence_ms` decides that you are finished because you have been quiet
for N milliseconds. There is no N that works. Short and it splits a thought;
long and every turn is sluggish. The operator's stored value is 700ms, which is
below a normal breath.

The previous round accepted this and built recovery instead: `reclaimTurn` takes
the turn back if you carry on. That is the right instinct and it is why voice is
usable at all today, but as a primary mechanism it is expensive and partial:

- every reclaim spends a full discarded model call plus its TTS renders, capped
  at 6
- it only fires while `spokenCount === 0`. Once the reply has spoken one
  sentence, carrying on your own sentence is classified as a barge, so one
  thought becomes two messages plus one wasted reply
- `incomplete()` with its `CONTINUATIONS` word set is a hand-written 1% version
  of a model that exists, is 8MB, and answers in 12ms (section 6.2)

### 2.3 Echo is subtracted from a level instead of cancelled

`MicStream` computes `net = rms - outputProbe() * echoK` and requires `net` to
clear `1.8 ×` threshold for 180ms before counting as a barge.

This cannot work in general, and not because the constants are wrong. An RMS
envelope has no delay model. The microphone hears the speaker's output 20 to
150ms after `outputLevel()` reports it, so a time-misaligned envelope is being
subtracted from the signal: it removes energy where there is no echo yet
(suppressing the user's real first syllable) and fails to remove it where there
is (leaking the assistant's voice into the VAD).

The browser has a real echo canceller, WebRTC AEC3, and `getUserMedia` is asked
for it. But AEC3 can only cancel audio it holds as a playout reference, and the
current playback path routes `new Audio(blobUrl)` through
`createMediaElementSource` into a Web Audio graph, which is exactly the routing
reported to break the reference. So the good canceller is asked for and then
disabled by the playback wiring, and a bad one is hand-rolled on top.

**The most important consequence:** `closeCleanWindow`, `spokeWhileThinking`,
the discard-on-completion rule, and the barge sustain window all exist only
because the microphone is assumed to be dirty while the assistant talks. That
assumption also produced the worst regression of the last session, when the
assistant's own reply was transcribed and sent as the operator's next message.
An entire subsystem, and the bug it caused, are compensation for not solving
echo. Solve echo and all of it is deleted rather than maintained.

### 2.4 The consequence of all three: state that cannot be reasoned about

`VoiceSession` carries 47 instance fields, and eight separate teardown blocks
(`stop`, `supersedeDraft`, `reclaimTurn`, `bargeIn`, `cancelTurn`,
`onReplyError`, `onThinkingTimeout`, `muteCurrentSpeech`) that each do some
subset of: bump the epoch, cancel the handle, detach the speaker, stop the
speaker, leave barge mode, clear the output probe, reset the sentence list. They
differ in small ways. The differences are where the bugs are.

That is what the operator is describing as disjointed systems competing with
each other, and it is a symptom rather than the disease. Fix 2.1, 2.2 and 2.3
and most of those fields stop being needed, because each of them exists to
remember something that a timestamped audio stream, a real endpoint decision, or
a clean microphone would simply answer.

---

## 3. Every symptom, traced

Reported or found in code. "Cause" refers to sections 2.1 to 2.4.

| # | symptom | cause | status |
|---|---|---|---|
| 1 | drops the middle of what I say | 2.1 | reduced last round, still live at every cut |
| 2 | one sentence arrives as three messages | 2.2 | mitigated by reclaim, still live past first spoken sentence |
| 3 | assistant's voice sent back as my message | 2.3 | fixed by compensation, structurally still possible |
| 4 | "No signal from the microphone" over silence | 2.4 | fixed (`voiceSignal.ts`), keep the rule |
| 5 | "Didn't catch that" after a cough | 2.4 | fixed, keep the rule |
| 6 | the mic button did nothing | 2.4 | fixed at the hook only. `VoiceSession.setPrefs` still calls `normalizeModes(pIn)` with no patch, so the same class of bug returns for any caller that is not `useVoiceSession` |
| 7 | slow, ~3s before it starts answering | 2.1 + 2.2 | not addressed |
| 8 | it is sluggish under load | 2.1 (quadratic preview competing with TTS) | not addressed |
| 9 | voice never saw typed messages | client owns the machine | seeded last round, remains a workaround |
| 10 | the reply appears twice in the log | server read race | patched client-side, server window still open |
| 11 | short replies never stream | `splitSentences` merges under 24 chars and `pushSentences` withholds the trailing fragment, so a one sentence answer emits nothing until the model finishes | not addressed |
| 12 | the "too quiet" nudge permanently raises sensitivity for the session, does not persist it, and leaves the settings slider showing the old value | 2.4 | not addressed |
| 13 | one knob, `barge_sensitivity`, sets the VAD onset threshold for *all* capture. Labelled as a barge control, it silently decides whether you are heard at all | 2.4 | not addressed |
| 14 | `forceEndUtterance()` no-ops silently when `endpointing` is already set, so the button reads as dead | 2.4 | not addressed |
| 15 | a new `Audio` element per sentence. On iOS a programmatic `play()` outside a gesture on a fresh element is blocked, so mobile Safari playback fails after the first turn | 2.1 | not addressed, and is the likely reason voice is worse on the phone |
| 16 | nothing works in the Expo app | the machine is 1600 lines of browser | not addressed |

Items 4, 5, 6 are corrections from the last round and are carried forward as
rules in section 9, not re-litigated.

---

## 4. The design in one page

> **Audio becomes one continuous timestamped stream that the server owns, and
> every decision becomes a function of that stream instead of a flag set by a
> timer.**

Four principles, in priority order. Where they conflict, the earlier wins.

**P1. One clock.** Every microphone sample has a monotonically increasing index
from the moment the session opens. A "segment" is a pair of sample offsets, not
a file. Cutting is arithmetic and therefore lossless. Anything that wants to
know what was said between two instants can ask.

**P2. One state machine, and it is pure.** The conversation is a reducer:
`(state, event) -> (state, effects)`. No timers inside it, no I/O, no mutable
flags outside it. Timers and sockets produce *events*; the reducer decides. It
is exhaustively testable without a browser, which is the only way this stops
regressing, given that every bug the operator reported after a "verified" claim
was in a path no test executed.

**P3. The client is a device, not a brain.** It captures, it plays, it renders
what the server tells it. It holds no conversation state. This is what makes
mobile a client rather than a rewrite, and it is why the reducer goes on the
server even though it could run in the browser.

**P4. Measure, do not infer. When you must guess, make the guess cheap to undo.**
Carried forward from the last round and generalised. Device health comes from
`MediaStreamTrack`, not from loudness. End of turn comes from a model, not a
stopwatch. Where a guess is unavoidable it must be recoverable without spending
a model call.

### Layers

```
L0  Device      client   mic -> AudioWorklet -> 20ms PCM frames
                         PCM frames -> ring buffer -> loopback AEC -> speaker
L1  Transport   both     one WebSocket. binary = audio, JSON = control
L2  Timeline    server   sample-indexed ring buffer, ~120s of session audio
L3  Perception  server   VAD -> speech segments; streaming ASR; turn-end model
L4  Conversation server  the reducer. the only place policy lives
L5  Generation  server   voice_worker, the resident `claude -p`. unchanged
L6  Speech out  server   streaming TTS by clause -> PCM frames down L1
```

L5 is the one part of today's system that is working and is kept as is.

---

## 5. L0 to L2: the capture spine

### 5.1 Client capture

```
getUserMedia({ audio: { echoCancellation, noiseSuppression, autoGainControl,
                        channelCount: 1 } })
  -> AudioContext(sampleRate: 16000)
  -> AudioWorkletNode("mic-tap")
  -> postMessage(Int16Array, 320 samples = 20ms)
  -> ws.send(header || pcm)
```

AudioWorklet rather than `MediaRecorder` or the deprecated
`ScriptProcessorNode`: it runs on the audio thread, so a busy main thread cannot
drop frames, and it produces raw PCM with no container, no codec negotiation, and
no Safari mp4 special case. The `REC_MIME_CANDIDATES` negotiation, `extForMime`,
`pickRecorderMime`, `snapshot()`, `cutSegment()`, `discardSegment()` and
`stopRecording()` all disappear.

Frame header, 8 bytes: `u32 sampleOffset | u16 seq | u8 flags | u8 reserved`.
The offset is the client's sample clock. It is what makes reconnect resumable
and what lets the server detect a real drop rather than guessing from timing.

The mic is opened once when voice turns on and is never stopped until voice
turns off. Muting the input lane sets a flag that stops *sending*; it does not
close the device, so unmuting is instant and cannot fail with a fresh permission
error.

### 5.2 Client playback

One persistent graph, created inside the voice-on gesture and reused for the
whole session:

```
ws PCM frames -> jitter buffer -> AudioWorkletNode("speaker") -> gain
  -> MediaStreamAudioDestinationNode -> [loopback RTCPeerConnection] -> <audio>
```

Two things this fixes at once:

- **iOS autoplay.** One element, unlocked once from the toggle gesture, playing
  continuously. No per-sentence `new Audio()`, so nothing to be blocked later
  (symptom 15). No blob URLs, so the object-URL and graph-node churn goes too.
- **Echo.** See 5.3.

Stopping speech is dropping the jitter buffer and telling the server, which is
instant and needs no `renderAbort`, no `URL.revokeObjectURL` sweep, and no
`unwireGraph`.

### 5.3 Echo cancellation, properly

The playback stream is routed through a local `RTCPeerConnection` pair before
reaching the speaker, so the browser classifies it as remote-participant audio
and AEC3 takes it as the playout reference:

```js
const dest = ctx.createMediaStreamDestination();
speakerNode.connect(dest);
const pc1 = new RTCPeerConnection(), pc2 = new RTCPeerConnection();
pc1.onicecandidate = e => e.candidate && pc2.addIceCandidate(e.candidate);
pc2.onicecandidate = e => e.candidate && pc1.addIceCandidate(e.candidate);
pc2.ontrack = e => { audioEl.srcObject = e.streams[0]; };
pc1.addTrack(dest.stream.getAudioTracks()[0], dest.stream);   // addTrack, not addStream
// ...offer/answer between pc1 and pc2, all local, no network
```

This is a known technique with an independent demo showing it working in
Chrome, Firefox and Safari, and it is patented prior art in the conferencing
world, so it is not exotic. It costs one local peer connection and a few ms of
added latency.

What it deletes: `echoK`, `outputProbe`, `setOutputProbe`, `bargeMode`,
`bargeSustainMs`, the `1.8 ×` elevated threshold, `closeCleanWindow`,
`spokeWhileThinking`, `discardSegment` and the discard-on-completion rule, and
the entire class of bug where the assistant hears itself.

**It must be verified before anything is deleted.** The test is mechanical and
belongs in the build: play a known TTS phrase at normal volume through the
speakers with the mic live, and assert the server-side ASR over that window
returns empty. Run it on desktop Chrome, desktop Firefox, iOS Safari and Android
Chrome. If a platform fails, that platform falls back to half-duplex (mic gated
shut while the assistant speaks, barge-in via the on-screen stop button only)
and *says so in settings*. Half-duplex that admits it beats full-duplex that
hallucinates.

In the Expo app this layer is replaced by the platform, which does it better:
`AVAudioSession` in `.playAndRecord` with mode `.voiceChat` on iOS, and
`VOICE_COMMUNICATION` on Android, both give hardware AEC. The contract above L0
does not change.

### 5.4 Server timeline

A per-session ring buffer of 16kHz mono int16, 120 seconds (3.8 MB). Absolute
sample offsets index into it. Everything downstream refers to ranges:
`utterance = [12_800, 91_200)`. Late-arriving frames are placed by offset rather
than appended, so a network hiccup reorders instead of corrupting.

This is the object that replaces roughly a dozen of the session's boolean fields
with a question anyone can ask.

---

## 6. L3: perception

Three models, all ONNX, all CPU, all already supportable here (`onnxruntime`
1.27 is installed, 24 cores, 36GB free).

### 6.1 VAD: Silero, server-side

`silero_vad.onnx`, about 2MB, ~1ms per 32ms frame, no torch required. It
replaces the RMS threshold, the noise-floor calibration, the sensitivity knob,
and `applyThreshold`. Silero is trained on noise; a threshold over an RMS is not,
which is why the current one needs calibrating, nudging, and a "mic level is low"
message.

The client keeps a *display-only* level meter for the orb. It stops being an
input to any decision.

### 6.2 End of turn: smart-turn-v3

`pipecat-ai/smart-turn-v3`. Whisper-tiny encoder plus a linear head, 8M
parameters, 8MB int8 ONNX, BSD-2-Clause, reported 12ms on CPU. It takes raw
waveform, not a transcript, and answers one question: has this speaker finished
their turn.

How it is wired:

```
VAD says silence has lasted 150ms
  -> run smart-turn over the last ~8s of the utterance
  -> p(complete) >= 0.5  -> ENDPOINT now
  -> p(complete) <  0.5  -> keep listening, re-ask at the next VAD pause,
                            and hard-endpoint at a 4s silence ceiling
```

This is the single highest-leverage change in the document. It gives a fast
endpoint on a finished sentence *and* patience on a mid-thought pause, which no
single timer value can do. It retires `endpoint_silence_ms`, `eager_endpoint`,
`eager_silence_ms`, `incomplete()`, `CONTINUATIONS`, and most of the reason
`reclaimTurn` has to exist.

`reclaimTurn` itself is **kept**, because a model at 0.5 is still a guess and P4
says a guess must be recoverable. It just becomes rare instead of routine, and
with streaming ASR (6.3) a reclaim no longer costs a re-transcription.

### 6.3 ASR: streaming, not batch

Whisper is a batch model. Asking it for a live transcript is what produces the
quadratic preview loop, the throwaway `tiny.en` pass, the re-decode at the
endpoint, and the hallucinated stock phrases over silence.

Target: an incremental recogniser fed the PCM stream, emitting stable and
unstable text as it goes. In 2026 the two credible self-hosted options are
**Moonshine** (streaming variants, smallest footprint, explicitly built for
latency-critical edge use, English medium streaming at 6.65% WER) and **NVIDIA
Parakeet TDT 0.6B v3** (transducer, streaming-native, 6.32% WER, no silence
hallucination). Parakeet is the accuracy pick if a GPU is available; Moonshine
is the pick for CPU.

Consequences: the caption is free rather than quadratic, the endpoint only has
to decode the last few hundred ms rather than the whole utterance, and the
hallucination filter in `voice_engine._is_speech` becomes a safety net rather
than a load-bearing part (Parakeet in particular does not hallucinate over
silence, which is the failure it was written for).

**Whisper stays as the fallback tier**, used when the streaming model is
unavailable. The hallucination filter is kept for it. This is a swap behind one
interface, not a rewrite: `transcribe(range) -> text` is the contract either way.

---

## 7. L4: the conversation reducer

### 7.1 The core modelling change

Today the machine is `listening -> thinking -> speaking`, strictly serial. But
**the user speaking and the agent responding are concurrent, not alternating.**
Every flag with "while" in its name (`spokeWhileThinking`) exists to smuggle
concurrency into a serial model.

Model two tracks:

```
User  = Silent
      | Speaking  { from: sample }
      | Ended     { range, transcript, decidingEndpoint }

Agent = Idle
      | Generating { turn, utterance }
      | Speaking   { turn, sentences, heardUpTo }
```

State is the pair. Events are: `SpeechStart`, `SpeechEnd`, `TurnComplete(p)`,
`Transcript(range, text, stable)`, `ReplyDelta`, `ReplyDone`, `ReplyError`,
`AudioPlayed(n)`, `TypedSend(text)`, `MicLane(on)`, `OutputLane(on)`,
`DeviceLost`, `Stop`. Effects are: `StartTurn`, `AbandonTurn(reason)`,
`SpeakClause`, `StopSpeaking`, `Emit(snapshot)`.

### 7.2 The whole interaction policy, in one table

| user starts speaking while... | decision | why |
|---|---|---|
| Agent Idle | begin utterance | ordinary |
| Agent Generating, nothing heard yet | **continuation.** abandon(discard), prepend the transcript so far, keep listening | they cannot be reacting to something they have not heard |
| Agent Speaking, `heardUpTo > 0` | **interruption.** stop speaking, report heard count, begin a new utterance | they heard it and answered it |

| the endpoint fires while... | decision |
|---|---|
| Agent Idle | start a turn |
| Agent Generating | abandon(discard) and start a turn with the joined text |
| Agent Speaking | already handled as an interruption above |

That is the entire policy. It is the rule `speechStartAction` already found, and
that rule is correct and survives: **the dividing line between a continuation and
an interruption is whether anything was heard, not how much time passed.** What
changes is that it is now the *only* rule, applied by one reducer, instead of
being one branch among eight teardown paths.

`abandon(reason)` is one function with one behaviour. `discard` never persists
the utterance, which is what keeps a re-sent turn from duplicating in the
transcript. That invariant is carried forward from the last round.

### 7.3 Why the server

Moving the reducer to the server is what makes this a system rather than three
implementations:

- **Mobile is a client, not a port.** The Expo app implements L0 and L1 only.
- **Voice and text are genuinely one conversation.** Today a voice worker had
  never seen a typed message and had to be seeded with a replay of the thread.
  Server-side, they are the same object.
- **The duplicate-reply race closes.** One process owns both the log and the
  active run.
- **A turn survives the tab.** Today closing the tab kills the machine mid-turn.
- **The reducer is testable without a browser**, which is the point of P2.

The client keeps exactly two local behaviours, because both must respond faster
than a round trip: the level meter, and an optimistic local mute of playback when
the stop button is pressed (the server confirms).

---

## 8. L6 and the latency budget

Current serial path after the last word:

```
wait out endpoint silence            700ms   (operator's stored value)
MediaRecorder stop round trip         20-80ms
upload + base.en over the WHOLE utterance   ~1800ms for 6s of speech
claude -p first token                 300-600ms
accumulate a COMPLETE sentence        the whole reply, if it is one sentence
Kokoro synthesis of that sentence     200-400ms
blob -> objectURL -> new Audio -> play  20ms+
```

Designed path:

| stage | budget | how |
|---|---|---|
| VAD silence before asking | 150ms | Silero |
| turn-end decision | 12ms | smart-turn-v3 |
| ASR tail only | ~50ms | streaming; the rest is already decoded |
| LLM first token | 300-600ms | resident worker, warm. dominant, not ours |
| first clause | ~0 | emit at the first comma or 6 words, not a sentence |
| TTS first chunk | 150-250ms | Kokoro on one clause |
| playback schedule | 20ms | jitter buffer already open |
| **total** | **~700 to 1100ms**, LLM dominated | |

Two changes carry most of it:

- **Emit on clause boundaries, not sentences.** `pushSentences` withholds the
  trailing fragment and `splitSentences` merges anything under 24 characters, so
  the voice prompt's requested "one to three sentences" is precisely the case
  where streaming never engages (symptom 11). Cut at `, ; : ` or 6+ words.
- **Wire the framed TTS stream.** `backend/routes/voice.py` already has the
  `stream: true` branch that emits length-prefixed WAV frames per sentence, and
  it has **no caller**. Under this design it emits PCM frames per *clause* down
  the same WebSocket, and `ServerSpeaker` is replaced by the jitter buffer, so
  the rewrite that made it not worth wiring last round is the work being done
  anyway.

---

## 9. Rules that carry forward

From the last round, each earned by a reported bug. They are re-stated as
properties of the new design rather than as patches.

1. **Silence is never an error.** Device health comes from `MediaStreamTrack`
   (`ended`, `muted`), never from level. `voiceSignal.ts` and its tests move
   across unchanged.
2. **Never accuse the operator of mumbling over noise.** An empty transcript is
   only reportable when the VAD saw real speech. Under Silero this becomes a
   property of the segment rather than the `lastSpeechMs` heuristic.
3. **A turn that will be re-sent is discarded, never cancelled**, so it cannot
   duplicate in the transcript.
4. **A run whose reply is committed is finished**, whatever `active_run` says.
   Fix this server-side as well this time (read `meta` after `read_events`),
   rather than only in `transcriptBlocks.ts`.
5. **`mic_enabled` and `speak_replies` stay derived**, and the derivation takes
   the patch. Better: under the new settings model (section 11) they are dropped
   from the client entirely and kept only as an API compatibility projection, so
   the bug class ends rather than being guarded.
6. **Voice and text are one conversation.** Structural once L4 is server-side.
7. **Whisper invents stock phrases over silence.** Keep `_is_speech` for the
   Whisper fallback tier.

---

## 10. Build order

Each phase is independently shippable and independently valuable. Nothing is
deleted until its replacement is proven on the operator's own origin.

**Phase 1: the capture spine.** AudioWorklet PCM, the WebSocket, the server ring
buffer, Silero VAD. The existing turn logic is repointed at the timeline
(`transcribe(range)`) and otherwise left alone. Deletes the cut gap and the
quadratic preview. This is the load-bearing phase; everything else is smaller.

**Phase 2: echo.** Loopback AEC, verified by the mechanical test in 5.3 on four
platforms. Only on a pass do `closeCleanWindow`, `spokeWhileThinking`,
`outputProbe`, `echoK` and the barge sustain window get deleted.

**Phase 3: turn detection.** smart-turn-v3 behind the VAD. Retires four settings
and `incomplete()`. `reclaimTurn` stays as the safety net.

**Phase 4: the reducer moves.** Port the machine to the server as a pure
reducer with an exhaustive test suite. The client shrinks to a device. This is
the phase that makes phase 7 possible and it is the largest one.

**Phase 5: streaming speech out.** Clause-level emission, PCM frames, jitter
buffer playback. Kills `ServerSpeaker`, the blob URLs, and the per-sentence
graph nodes. Fixes iOS playback after the first turn.

**Phase 6: streaming ASR.** Moonshine or Parakeet behind the existing
`transcribe` interface, Whisper kept as fallback.

**Phase 7: the Expo client.** L0 and L1 only, against the same contract.

Phases 1, 2, 3 and 5 each remove a class of reported bug on their own. Phase 4
removes none directly and is the reason none of them come back.

---

## 11. Settings, reduced

Eight of the current knobs exist to tune mechanisms this design removes. The
settings sheet should end up as:

| keep | drop, and why |
|---|---|
| input mode (voice / text / both) | `endpoint_silence_ms` → smart-turn decides |
| output mode (voice / text / both) | `eager_endpoint`, `eager_silence_ms` → same |
| voice, speed | `barge_sensitivity` → Silero; it was silently the master input gate (symptom 13) |
| **how soon it replies**: snappy / natural / patient, one 3-way that biases the smart-turn threshold | `barge_in` → always on where AEC is verified, off where it is not, stated rather than configured |
| test microphone, and the diagnostics readout | `typing_hold_ms`, `enter_interrupts` → not surfaced today anyway |

A knob whose correct value the system can measure is a bug wearing a control.
`barge_sensitivity` is the clearest case: it is presented as an interruption
preference and is actually the threshold that decides whether you are heard at
all.

---

## 12. Open questions

Named rather than assumed, and each has a default so none of them blocks phase 1.

1. **Does loopback AEC hold on iOS Safari with getUserMedia forcing output to
   the speaker?** iOS routes to the loudspeaker while capturing, which is the
   hardest case for any canceller. Default if it fails: half-duplex on iOS web,
   stated in settings, with the Expo app (hardware AEC) as the real answer on
   phones.
2. **WebSocket or WebRTC for L1?** WebSocket is far simpler and is fine on a LAN
   or a good link. WebRTC gives adaptive jitter handling and packet loss
   concealment on a bad one. Default: WebSocket for phases 1 to 6, with L1
   isolated behind an interface so WebRTC is a swap and not a rewrite.
3. **Parakeet needs a GPU to be worth it; Moonshine does not.** Default:
   Moonshine on CPU, measure, revisit.
4. **What is a clause for TTS?** Comma, semicolon, colon, or 6 words, whichever
   comes first, never splitting inside a number or an abbreviation. Needs a
   listening test; it is the difference between fluent and choppy.
5. **Should a continuation merge even after the assistant has been heard?**
   Section 7.2 says no, because it is not decidable from timing. It may be
   decidable from content later, using the same turn model. Not in scope.

---

## 13. What this replaces

For the next reader, so nothing is half-migrated:

- `docs/voice-mode/design.md` (2026-07-11) described the architecture now
  implemented. Sections 1 to 4 of it are superseded here. Its section 5
  (modality matrix) and its heard-versus-generated definition survive intact.
- `docs/voice-mode-handoff-2026-08-12.md` is the record of the last fix round.
  Its invariants are section 9 here. Its "not done" list is sections 8 and 10.
- `docs/voice-mode/redesign-2026-07-13.md` and `redesign-2026-07-25.md` are
  historical.

---

## Sources

- [LiveKit: turn detection for voice agents](https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection)
- [LiveKit: improved end-of-turn model cuts interruptions 39%](https://livekit.com/blog/improved-end-of-turn-model-cuts-voice-ai-interruptions-39)
- [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)
- [Daily: Smart Turn v2, open source semantic VAD](https://www.daily.co/blog/smart-turn-v2-faster-inference-and-13-new-languages-for-voice-ai/)
- [Pipecat: speech input and turn detection](https://docs.pipecat.ai/pipecat/learn/speech-input)
- [Pipecat: user turn strategies](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies)
- [The echo cancellation problem nobody talks about](https://dev.to/remi_etien/i-built-a-voice-ai-with-sub-500ms-latency-heres-the-echo-cancellation-problem-nobody-talks-about-14la)
- [nguyenvulebinh/browser-aec, loopback AEC demo](https://github.com/nguyenvulebinh/browser-aec)
- [How WebRTC AEC3 works](https://switchboard.audio/hub/how-webrtc-aec3-works/)
- [Loopback audio channels for echo cancellation in web browsers (US11501791B1)](https://patents.google.com/patent/US11501791)
- [Coval: voice AI echo cancellation, causes and fixes](https://www.coval.ai/blog/voice-ai-echo-cancellation/)
- [Northflank: best open source STT in 2026, with benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Best local speech-to-text models 2026: Moonshine vs Parakeet vs Whisper](https://www.onresonant.com/resources/local-stt-models-2026)
- [MediaRecorder vs Web Audio API for browser audio streams](https://medium.com/@tihomir.manushev/how-browsers-handle-audio-streams-mediarecorder-vs-web-audio-api-72553933a3a2)
- [Switching a voice agent from WebSocket to WebRTC](https://dev.to/aws-builders/switching-my-ai-voice-agent-from-websocket-to-webrtc-what-broke-and-what-i-learned-3dkn)
- [MediaRecorder on iPhone Safari: format differences](https://www.buildwithmatija.com/blog/iphone-safari-mediarecorder-audio-recording-transcription)
- [iOS Safari forces audio output to speakers when using getUserMedia](https://medium.com/@python-javascript-php-html-css/ios-safari-forces-audio-output-to-speakers-when-using-getusermedia-2615196be6fe)

---

## 14. As built, 2026-08-12

### Where the code is

| file | holds |
|---|---|
| `backend/services/voice/timeline.py` | the sample-indexed ring buffer. `Span`, `read`, `write_at`, `tail` |
| `backend/services/voice/machine.py` | **the whole policy**, as a pure reducer. No timers, no I/O |
| `backend/services/voice/detectors.py` | `SpeechDetector` (Silero or energy), `TurnEndDetector` (smart-turn or ladder) |
| `backend/services/voice/clauses.py` | clause splitting and `clean_for_speech` |
| `backend/services/voice/runtime.py` | the impure half: perception loop, ASR, generation, speech out |
| `backend/services/voice/protocol.py` | the 8-byte frame header, and cursor unwrapping |
| `backend/routes/voice_stream.py` | `/voice/stream`, the one socket. Auth + Origin check live here |
| `backend/workshop_public_app.py` | the edge's WebSocket proxy (see below) |
| `frontend/public/voice-*-worklet.js` | capture (resample to 16k, 20ms frames) and playback (jitter buffer) |
| `frontend/src/lib/voice/` | `capture`, `playback`, `transport`, `client`, `protocol`, `modes`, `errors` |

Deleted: `frontend/src/lib/voiceSession.ts` (1600 lines), `frontend/src/lib/voice.ts`
(1126), `voiceTurnAction.ts`, `useReplySpeaker.ts` (which had no callers at all),
and the per-turn REST surface in `routes/voice.py`.

### Two things landed differently from the plan

1. **The decision tick is a clock, not a frame arrival.** Committing,
   captioning and the turn-end question were originally driven from `on_audio`.
   That looked equivalent, since frames arrive at 50fps, and was not: with the
   client stalled or a background tab throttled, the endpoint was never
   re-evaluated and the utterance hung with nothing to un-hang it. `_tick_loop`
   runs at 40ms independently of the transport. Decision cadence and transport
   cadence are different things; coupling them is a wedge waiting to happen.

2. **The edge needed a WebSocket proxy.** `workshop_public_app.py` is an httpx
   reverse proxy and httpx cannot carry an Upgrade. Without the new
   `proxy_ws` route the socket would have worked perfectly on the Vite dev
   origin and failed on the BUILT origin, which is the one the operator uses.
   Vite also needed `ws: true`, which it does not default to. Both are covered
   by `tests/test_edge_websocket_proxy.py`.

### The open question that got answered

**Question 3 (streaming ASR) is unresolved and question 2 (WebSocket vs WebRTC)
is settled as WebSocket**, behind `transport.ts` so it stays a swap.

The perception models are **not installed**. `onnxruntime` is present and both
models are tiny, but this process cannot fetch them (egress from the shell is
blocked by policy, correctly). So both detectors ship with a real local
implementation and auto-upgrade the moment weights appear at:

```
.runtime/voice/models/silero_vad.onnx      ~2 MB
.runtime/voice/models/smart-turn-v3.onnx   ~8 MB
```

`GET /api/voice/health` reports which tier is live, and the settings sheet shows
it, so the surface never implies accuracy the box does not have. The fallbacks
are not placeholders:

- **Speech detection** is an asymmetric noise-floor tracker with hysteresis. It
  starts its floor at ZERO and raises it slowly, deliberately: the obvious
  design measures the room first, and if the operator is already talking when
  the session arms it calibrates on their voice and goes silently deaf for the
  rest of the session. That exact bug was in the old client's 1200ms calibration
  pass. Starting low inverts the error, and "it heard something it shouldn't" is
  recoverable where "it cannot hear you" is not.
- **Turn detection** is a silence ladder crossed with linguistic completeness:
  a finished sentence endpoints at 380ms, an unpunctuated one at 900ms, one
  ending mid-clause waits 2200ms, and 4s ends anything. That ladder is most of
  what smart-turn buys, and no single `endpoint_silence_ms` can produce it.

### Verification

```
cd backend && .venv/bin/python -m pytest tests/ -q -k "voice or edge"   # 123
cd frontend && npm test                                                # 524
npm run typecheck                                                       # clean
bash scripts/check_design.sh                                            # clean
bash scripts/build_workshop_public.sh                                   # then ui_sync
```

The five failures in the full backend run (`test_chat_cut_points`,
`test_chat_message_ordering`, two in `test_chat_run_lifecycle`,
`test_paths_are_portable`) are the pre-existing attachments WIP, unchanged.

`uvicorn 0.32.1` + `websockets 16.0` was verified to carry binary frames over a
real loopback server, because websockets 14 removed the legacy API uvicorn's
implementation uses and a silent failure there would have meant voice could not
connect at all.

**Not verified by actually using voice mode.** Everything above is tests and
traces. In particular the loopback echo canceller has never faced a real room:
`snapshot.echoCancelled` reports whether it established, and the settings sheet
says plainly whether talking over the reply will work, but whether AEC3 actually
suppresses Kokoro through real speakers is the first thing to check.

### What to do next

1. Use it, and watch `echoCancelled` in Diagnostics. If it is off, barge-in is
   the stop button only, and that is what the sheet says.
2. Drop the two ONNX files in and restart. Nothing else changes.
3. Streaming ASR (Moonshine or Parakeet) behind `_transcribe`, which is already
   the only seam it needs.
