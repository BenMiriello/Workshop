# Voice mode: handoff, 2026-08-12

Written at the operator's request to survive a compaction. Everything here is
uncommitted working-tree state unless it says otherwise.

Read this before touching anything under the voice path. Several of the changes
below LOOK like they could be simplified back, and are load-bearing; the ones
that bit hardest are called out with the symptom they produced.

---

## 1. What voice mode is, in files

| file | owns |
|---|---|
| `frontend/src/lib/voice.ts` | `MicStream` (getUserMedia + VAD + MediaRecorder), the speakers (`ServerSpeaker` / `BrowserSpeaker` / `NativeSpeaker`), `transcribeBlob`, mic preflight + error classification |
| `frontend/src/lib/voiceSession.ts` | the turn state machine: `idle -> connecting -> listening -> thinking -> speaking`. Everything below is about this file |
| `frontend/src/lib/useVoiceSession.ts` | React binding, prefs persistence, the transport to the backend voice worker |
| `frontend/src/lib/voiceTurnAction.ts` | **NEW.** What starting to talk MEANS, given what the assistant is doing |
| `frontend/src/lib/voiceSignal.ts` | **NEW.** Is the input lane broken, or has nobody spoken yet |
| `frontend/src/components/chat/useVoiceComposer.ts` | orb icon/label + the status strip text |
| `frontend/src/components/chat/VoiceControls.tsx` | the strip and the orb/mic/settings cluster |
| `frontend/src/components/chat/VoiceTurn.tsx` | the spoken turn as bubbles in the log |
| `frontend/src/components/VoiceSettings.tsx` | the settings sheet |
| `backend/services/voice_worker.py` | one resident `claude -p` per voice session, fed over stdin |
| `backend/services/voice_engine.py` | faster-whisper STT + Kokoro TTS |
| `backend/routes/voice.py` | `/api/voice/*`: health, stt, tts, session, turns, prefs |

Both local engines are confirmed up on this box: `base.en` STT (`tiny.en` for
live previews), Kokoro TTS. None of the problems below were an engine fallback.

---

## 2. The rule that most of this turns on

**A microphone cannot tell you whether a person is finished, and a level cannot
tell you whether a device is alive.** Nearly every bug in this session was some
version of inferring intent or health from loudness, on a timer, before the
evidence existed.

Three places now refuse to guess:

1. **Is the operator finished?** Do not decide from silence alone. Decide, and
   then let carrying on TAKE THE TURN BACK (`reclaimTurn`). By then they have
   demonstrated it.
2. **Is the mic dead?** Ask the `MediaStreamTrack`, which knows (`ended` =
   device gone, `muted` = another app took it). Silence is never evidence.
3. **Is the mic clean?** It is clean until the first spoken sample and echoey
   after it. That is a known instant, not a threshold.

---

## 3. Regressions introduced and fixed IN THIS SESSION

Do not reintroduce these. Both were mine, both shipped, both were reported.

### 3a. The echo feedback loop

Making the endpoint stop deafening the mic (`cutSegment` instead of
`stopRecording`) meant ONE recorder segment now spans thinking AND speaking.
The first version kept that segment whenever speech was detected during
thinking. It contains the assistant's entire spoken reply as microphone echo,
so the assistant's own voice was transcribed and sent back as the operator's
next message.

Fixed by `closeCleanWindow()`, called from `onSentenceStart(0)` (immediately
before `play()`, the only point that tracks when the room stops being quiet).
Audio before that instant is the operator's and is transcribed; audio after it
is echo and is discarded at completion.

**If you ever keep the post-endpoint segment past the first spoken sample, you
have rebuilt this bug.**

### 3b. The dead mic button

`mic_enabled` is DERIVED (`normalizeModes`: `mic_enabled = input_mode !== "text"`).
The composer's mic button patches `mic_enabled` and nothing else, so its value
was recomputed from an `input_mode` nobody changed and discarded on the same
line. The button did nothing **in both directions**, and the operator was stuck
on "Mic off" with the only control that claimed to fix it incapable of it.

Fixed by giving `normalizeModes` the `patch` argument, mirroring
`_normalize_modes` in `backend/routes/voice.py`, which always had it right:
whichever side the caller wrote wins. `useVoiceSession.updatePrefs` passes it.

**The mic button is the only control in the app that writes a derived prefs
field.** I checked every other `updatePrefs` call; the rest write source-of-truth
fields. If you add a control that writes `mic_enabled` or `speak_replies`, it
depends on that patch argument.

---

## 4. What changed, and why it is not obvious from the diff

### Capture (the "nightmare core")

- **The recorder never stops at the endpoint.** `endpointUtterance()` uses
  `mic.cutSegment()`, which returns the finished audio and starts a fresh
  recorder in one breath. It used to `stopRecording()`, leaving the lane deaf
  across the whole STT round trip and, on a real turn, the entire reply. Speech
  in that window was not delayed, it was gone. This is the "it drops the middle
  of what I say" report.
- **`endpointing` single-flight guard.** Because the endpoint is no longer
  instantaneous, a second VAD end can land mid-pass and would cut the segment
  out from under the first, splitting one utterance in two.
- **`reclaimTurn()` merges continuations.** Speech during `thinking` with
  `spokenCount === 0` cancels the turn with reason `discard`, puts the
  already-transcribed words back into `segs`, and returns to listening. The
  whole thing then lands as ONE message. This is the operator's own design ask
  ("combine my continuous speaking into one message"). Capped at 6 reclaims per
  utterance (each spends a discarded model call).
- **`endpoint_silence_ms` 900 -> 1400 -> 1000.** The 1400 was a mistake: it
  bought fewer splits by making every turn sluggish and still split anyone who
  paused longer. With reclaim, firing early is recoverable, so the threshold
  only has to be close, not right. NOTE: the operator's stored prefs have a
  custom 700, so defaults do not affect him.

### Errors that fired when nothing was wrong

- **"No signal from the microphone"** fired whenever the operator did not speak
  within a 4s window. Replaced by `signalVerdict()` reading track state.
  Reported twice. See `voiceSignal.ts` for the full reasoning.
- **"Didn't catch that"** fired on any segment that transcribed empty, so a
  cough or a door accused the operator of mumbling. Now gated on
  `mic.lastSpeechMs >= 350` (real VAD speech duration, added to `MicStream`).
- **Mic permission denial** set BOTH `micFail` and `error`, so one refusal
  arrived on two channels and rendered in the red per-turn slot with a Retry
  that could not help. Now `micFail` only, rendered as a neutral notice.

### Display

- **The live transcript is a bubble at the foot of the log** (`VoicePendingTurn`),
  not a line in the strip. The strip had `white-space: nowrap` + ellipsis, so it
  showed the FIRST few words and never the latest; and because it lives inside
  the composer, growing it pushed the action row down. The bubble wraps, the log
  is already pinned to the bottom, and it takes its space from the transcript.
  The strip now carries fixed-size status only.
- Muted output says so ("Listening. Replies in text"). It was invisible: you
  talk, text arrives, nothing explains the silence.

### Backend

- **Whisper hallucination filter** (`voice_engine._is_speech`). Whisper does not
  return nothing for silence, it returns its most likely caption, which is a
  short stock phrase. The operator was sent "Bye bye." for words he never said.
  The phrase list ALONE is not the filter: a stock phrase is dropped only when
  `no_speech_prob` or `avg_logprob` also says the decoder was unsure, so a
  confidently decoded goodbye survives.
- **Voice sessions are seeded with the thread** (`voice_worker._thread_seed`).
  See section 5, this is the big one.
- **The voice session id is persisted** (`voice_session` on the thread). `WORKERS`
  is an in-memory dict, so a backend restart destroyed the only copy and the next
  utterance minted a fresh session, forgetting every spoken turn. `/clear` drops
  it alongside `claude_session`.

### Chat transcript (adjacent, not voice)

- **The duplicate reply.** `chat_log.read_thread` reads `meta` BEFORE the events,
  while `commit_run` appends the reply and clears `active_run_id` after. A read
  landing in that gap returns the committed reply AND `active_run` still naming
  the run that produced it. The client believed it and rendered that reply again
  below the new message, marked `working`. Fixed in `transcriptBlocks.ts` via
  `liveRunId`: a run whose reply the log already carries is finished, whatever
  the snapshot says. **The server-side window is still open** (see section 7).

---

## 5. The finding that matters most, and is only half fixed

**A conversational voice worker had never seen a single typed message in the
chat.** It spawns its own `claude -p` with its own session id, and
`_with_context` sent only the live-context block plus the utterance. The
operator asked directly on a call ("are you able to see the past messages before
this particular audio session?") and the answer was no, and could never have
been yes.

There IS a branch meant to prevent this, which shares the agent's own session,
but it is gated on a `build:` context key and **zero of the nine threads in the
store that have ever used voice have one**. A project chat is keyed `development`
or `project:notes-app` and falls straight through to the fresh-session path,
which the comment there already admits it does.

Fixed by seeding, and the seed honours the `/clear` boundary and skips a
half-written reply in flight. But note the cost: the seed is prepended to the
first spoken utterance, and on a real thread that is ~21k tokens. It is not
capped, deliberately, matching the operator's decision that transcripts are sent
whole. **If the first spoken turn of a session feels slow, this is why.**

### The sibling finding, handed to another chat

`backend/routes/chat.py` slices `history[-MAX_HISTORY:]` with `MAX_HISTORY = 60`
and automatic compaction was removed on 2026-08-11, so anything older is now
**silently dropped with nothing summarising it**. Where a summary does exist, the
dropped range sits BETWEEN the summary and the kept tail: a hole in the middle.

The cap is measured in the wrong unit and the store proves it: the largest
transcript is ~110k tokens in THIRTEEN messages, so it passes the 60-message cap
untouched, while a 129-message thread at ~52k tokens gets amputated. Nothing in
the store exceeds 11% of a 1M window.

**The operator moved this discussion to another chat.** That session owns
`routes/chat.py`, `chat_store.py`, `useCompaction.ts` and `lib/chat.ts`, and is
removing compaction, the message cap, and distillation entirely. Do not do that
work here. One hunk of mine lives in their file: `voice_session=None` in
`chat_store.clear_thread_context`. It is audio work and must be preserved.

---

## 6. Invariants. Breaking any of these reintroduces a reported bug

1. The recorder is not stopped at the endpoint.
2. Audio recorded after the first spoken sample is echo and is never transcribed
   as the operator.
3. Silence never produces an error.
4. A turn the client is about to re-send is cancelled with `discard`, so it does
   not persist and duplicate.
5. `mic_enabled` / `speak_replies` are derived. Writing one requires the patch
   argument to `normalizeModes`.
6. A run whose reply is committed in the log is finished, whatever `active_run`
   says.
7. Voice and text are ONE conversation. A fresh voice session gets the thread.

---

## 7. Not done

- **Latency, all of it.** A turn serially waits out the silence, then a full
  transcription of audio the preview pass already transcribed and threw away,
  then whole-sentence synthesis before a single sample plays. Worth ~400-800ms.
  `backend/routes/voice.py` already has a framed streaming TTS branch that **has
  no caller**; wiring it means rewriting `ServerSpeaker` from blob-URL playback
  to scheduled Web Audio buffers. Deliberately not started in the same pass as
  behavioural fixes.
- **The one-sentence case.** The voice system prompt asks for "usually one to
  three sentences", and for a one-sentence reply NOTHING is synthesized until
  the model completely finishes (`pushSentences` withholds the trailing
  fragment; `splitSentences` merges anything under 24 chars). The streaming
  design does not engage in what the prompt makes the common case.
- **`lastTurnMs` is stamped after the silence and the transcription**, so the
  latency figure in the diagnostics footer has never shown the wait the operator
  actually feels.
- **`read_thread`'s non-atomic `active_run`** (section 4). Fixed client-side
  only. Swapping the read order so `active_run` is computed from a meta read
  AFTER `read_events` would narrow the window to the inter-write gap.
- **Nothing here has been verified by actually using voice mode.** Everything is
  traced from code and covered by unit tests. Every bug the operator reported
  after my "verified" claims was in a path no test executed.

---

## 8. Verification

```
npm run typecheck                      # from repo root, kit + frontend
cd frontend && npm test                # 577 tests, 39 files
bash scripts/check_design.sh
cd backend && .venv/bin/python -m pytest tests/test_voice_sees_the_thread.py \
                                          tests/test_voice_hallucination_filter.py -q
bash scripts/build_workshop_public.sh  # the operator uses the BUILT origin
bash scripts/ui_sync.sh                # prove all three origins match
```

**Five backend tests fail and are NOT from this work**: `test_chat_cut_points`,
`test_chat_message_ordering`, two in `test_chat_run_lifecycle`, and
`test_paths_are_portable`. Verified by stashing: identical failures without any
of these changes. They come from the uncommitted attachments WIP, where
`build_streaming.inject()` gained a third argument the tests' spies do not take.

## 9. State

Everything is **uncommitted**, per this session's rule. New files:
`frontend/src/lib/voiceTurnAction.ts` (+test), `frontend/src/lib/voiceSignal.ts`
(+test), `frontend/src/lib/voiceModes.test.ts`,
`backend/tests/test_voice_sees_the_thread.py`,
`backend/tests/test_voice_hallucination_filter.py`.

Modified: `voice.ts`, `voiceSession.ts`, `useVoiceSession.ts`, `VoiceSettings.tsx`,
`VoiceControls.tsx`, `VoiceTurn.tsx`, `ChatLog.tsx`, `useVoiceComposer.ts`,
`transcriptBlocks.ts` (+test), `styles.css`, `packages/ui/src/styles/components.css`,
`backend/routes/voice.py`, `backend/services/voice_engine.py`,
`backend/services/voice_worker.py`, and one hunk in `backend/services/chat_store.py`.

`chat_store.py` and `routes/chat.py` also carry the OTHER session's in-flight
work. Stage selectively.
