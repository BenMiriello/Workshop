# Workshop Voice Mode — design + plan

Status: building (2026-07-10). This is the design the implementation is built to.

## What this is

A **voice interface mode** for the Workshop chat system, and a conversational
interaction modality that treats a spoken exchange as a real conversation rather
than a wall of streamed text. It layers on top of the existing chat (`Chat.tsx`,
`/chat` routes, the `ContextProvider` system) — it does not replace it. Every
place chat is available (any app dock, the focused-chat page, the build agents,
mobile web, and later the native app) can enter voice mode.

Two problems it solves, both stated by the operator:

1. **The text-dump problem.** Claude replies with a large block covering several
   threads at once. You want to respond to the first point, or interrupt, without
   losing the other threads. Text chat has no notion of "what you actually took
   in" — the whole block is just *there*.
2. **The modality problem.** A spoken assistant has properties a text dump lacks:
   it speaks slower than it "thinks," so it *knows what you actually heard* when
   you interrupt; it can hold un-said threads in the background; it can learn your
   patterns and pre-shape its next reply. We build the interface around those
   properties instead of fighting them.

## The core modality model

The unit is a **turn**, driven by a state machine (`voiceSession.ts`):

```
        ┌──────────── barge-in (user speaks) ─────────────┐
        v                                                 │
  idle → listening → (endpoint) → thinking → speaking ────┘
   ^                                            │
   └──────────── done, nothing queued ──────────┘
```

- **listening** — mic hot, VAD detects speech start/end (endpointing). Partial
  transcript shown live. Silence past the endpoint threshold ends the utterance.
- **thinking** — utterance sent to the chat backend (existing data/build turn).
- **speaking** — the reply is spoken **sentence by sentence** through a playback
  queue. The mic stays hot (barge-in).

**Heard-vs-generated (the key idea).** The model generates a whole reply, but TTS
voices it one sentence at a time. The session tracks, per turn:
- `generated` — the full reply text, split into sentences.
- `spoken` — the sentences actually voiced *and finished* before this moment.
- `heard` — `spoken` minus any sentence cut off mid-word by a barge-in.

When the user barges in, playback stops at the current sentence boundary; the
**remainder** (`generated − heard`) is not thrown away — it becomes a **background
thread** the assistant can offer later ("I hadn't gotten to the tax point yet —
want that now?"). This is the concrete answer to "I don't want to lose the other
tracks when I interrupt."

**Background-thread queue.** A reply often contains several distinct threads. Two
sources feed the queue:
1. **Interrupted remainder** — the unspoken tail of a barged-in reply.
2. **Explicit asides** — the assistant may mark secondary threads. We do this with
   a lightweight, optional convention in the voice persona: the model may end a
   reply with `⟦aside: <one-line topic>⟧` markers. The session strips these from
   the spoken text and lists them as offerable threads. (Convention only; if the
   model doesn't emit them, nothing breaks.)

The queue is visible in the UI and never auto-plays — surfacing an old thread must
never yank you off the current one. The assistant *offers*; you pull.

**Learning / adaptation (lightweight, single-user).** Per-owner `voice-prefs.json`
holds explicit prefs (voice, rate, STT/TTS provider, verbosity, barge-in
sensitivity) and a small rolling signal: how often the operator interrupts, and
how early. A high interrupt rate nudges the voice persona toward shorter replies.
No multi-user machinery, no cruft — just a JSON file and a sentence in the prompt.

## Architecture

Two provider tiers behind one `VoiceEngine`, auto-selected, user-overridable:

| | Server tier (default, best quality + fully private) | On-device tier (zero infra, mobile-friendly) |
|---|---|---|
| **STT** | `faster-whisper` (`base.en` int8), `POST /api/voice/stt` | Web Speech API `SpeechRecognition`; later native `expo-speech-recognition` (iOS on-device) |
| **TTS** | `kokoro-onnx` (Kokoro-82M), `POST /api/voice/tts` streaming per-sentence | `SpeechSynthesis`; later native `expo-speech` |

- **Device**: server models run on CPU by default (24 cores, fine for short
  utterances). `VOICE_DEVICE=auto` opportunistically uses CUDA via the existing
  **gpubroker** (`gpubroker_client`) when VRAM is free; never blocks on it, always
  falls back to CPU. `cpu`/`cuda` force it.
- **GPU warm-hold**: a session is slow only *once*, at the start. On the first
  turn of a chat/voice session the backend acquires a broker lease (non-blocking)
  and loads the model onto the GPU; each subsequent turn **re-arms** the lease with
  a short sliding TTL (~45s), so the GPU stays held until ~30s after the last turn,
  then lapses on its own (broker TTL expiry — no release timer to manage). If the
  lease isn't granted, the turn quietly runs on CPU. This warm-hold applies to the
  **text** chat path too, not just voice — same `GpuWarmHold.touch()` call.
- **Cold-start filler**: because the first turn may pay model-load + GPU-acquire
  latency, the client speaks an instant **stock filler** ("one moment…") via the
  always-available browser `SpeechSynthesis` while the real reply is being
  produced, so there is never dead air on a cold session. Optional server-side
  pre-rendered filler clips (cached under `.runtime`) can replace the browser
  voice once TTS is warm. For text, the existing streaming placeholder already
  covers this.
- **Graceful degradation**: if the server models aren't installed/loaded, the
  voice endpoints return `503` with `{available:false}` and the frontend silently
  falls back to the browser tier. Voice mode therefore works the moment the UI
  ships, and gets better when the models are present.
- **Native seam**: the mobile bridge already exposes an `audio` capability
  (`shared/native-bridge.ts`, `startCapture/stopCapture`). The engine prefers a
  native STT/TTS provider when `hasCapability("audio")`, so the native app can
  swap in on-device iOS speech later with no UI change.

## Backend surface (new)

- `backend/services/voice_engine.py` — lazy singletons for the STT and TTS models,
  device selection, sentence splitting. No import-time model load.
- `backend/routes/voice.py`, mounted at `/voice` (per-user, like chat):
  - `GET  /voice/health` → `{stt, tts, device, voices}` capability probe.
  - `GET  /voice/voices` → available Kokoro voices.
  - `POST /voice/stt` (multipart audio) → `{text}`.
  - `POST /voice/tts` `{text, voice?, speed?, format?}` → audio bytes (wav), or
    `stream=1` for chunked per-sentence audio.
  - `GET  /voice/prefs` / `PUT /voice/prefs` → per-owner `voice-prefs.json`.

## Frontend surface (new)

- `src/lib/voice.ts` — `VoiceEngine`: `transcribe()`, `speak(sentence)`, provider
  selection, capability probe. Wraps server + browser (+ native) providers.
- `src/lib/voiceSession.ts` — framework-agnostic turn-state machine + heard/queue
  bookkeeping + VAD. Emits events a React hook subscribes to.
- `src/lib/useVoiceSession.ts` — React binding.
- `src/components/VoiceMode.tsx` — the full-screen conversational surface: status
  orb, live transcript, current-sentence caption, interrupt affordance, and the
  offerable background-thread queue. Route `/voice/:contextKey`.
- `Chat.tsx` composer gets a **mic** button (push-to-talk dictation into the
  composer) and a **voice** button (enter full voice mode for this context). Both
  gated on engine availability; both degrade to the browser tier.

## Integration invariants

- Voice never forks the chat backend: a spoken utterance is an ordinary
  data/build turn through the same `submitText` path; the reply is the same
  message. Voice is a presentation + capture layer.
- Design-system law applies (`frontend/CLAUDE.md`): `src/ui` primitives, lucide
  icons (no emoji), tokens (no inline hex), four list states, reduced-motion.
- Single-user: no allowlists / gating / confirmations beyond what one user needs.

## Build phases

1. Backend `voice_engine` + `/voice` routes (STT, TTS, health, prefs), broker-aware
   device pick, graceful 503.
2. Frontend `VoiceEngine` + providers + capability probe.
3. `voiceSession` state machine (VAD, barge-in, heard-vs-generated, queue) + hook.
4. `VoiceMode.tsx` surface + route; mic/speaker buttons in `Chat.tsx`.
5. Install models, wire, restart backend, build frontend, drive end-to-end.
```
