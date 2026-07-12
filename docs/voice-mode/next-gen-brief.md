# Voice Mode v2 — starter brief (for the design pass)

Status: **starter brief**, written 2026-07-11. Grounds the redesign in the *actual*
current code, states the operator's goals, and lists the research directions the
design doc must resolve. A Fable design agent turns this into `design.md`; then we build.

The operator's framing (verbatim intent, paraphrased tight):
> Voice occasionally works but usually fails to respond or is slow. Make it
> faster / more reliable. Design the *system* of interruption, tracking what's
> been heard, predicting/acting/thinking in the background on separate
> threads/layers between and during turns. Add a setting for endpointing delay
> (how long a pause before it takes a turn). Maybe get fancier: fire an early
> speculative turn to a fast model in case the user is done, and if the user
> resumes, interrupt/replace it — quick response without cutting the user off.
> The Speech (auto/local/muted) setting looks disabled — fyi. And: **variable
> input/output modes** — voice-in/voice-out/text-in/text-out independently. e.g.
> listen to the reply but *type* responses, with the same interrupt cadence.
> Typing shouldn't interrupt speech; Esc interrupts; Enter sends (and interrupts
> if that setting is on). Configurable. This is a starting point — research
> existing solutions, there are known-good patterns.

---

## 1. What exists today (grounded, with file refs)

**Turn state machine** — `frontend/src/lib/voiceSession.ts`
- States: `idle → connecting → listening → thinking → speaking`, client-side.
- Heard-vs-generated tracking: the model returns a whole reply; we voice it one
  sentence at a time and mark each `pending | speaking | heard`. Already built.
- Barge-in: while speaking the mic stays hot; talking over it stops at the
  current sentence and the unspoken remainder becomes a queued "background
  thread" that is *offered, never auto-played*. `bargeIn()` / `finishSpeaking()`.
- Background-thread queue: interrupted remainders + explicit `⟦aside:…⟧` markers.
- Snapshot exposed to React via `useSyncExternalStore` (just fixed an infinite-loop
  bug: snapshot must be a cached, stable reference — see `snapshot()`), hook in
  `frontend/src/lib/useVoiceSession.ts`.

**Audio I/O** — `frontend/src/lib/voice.ts`
- STT providers: `native` (on-device) → `server` (faster-whisper) → `browser`
  (Web Speech). TTS: `native` → `server` (Kokoro) → `browser`. All local. Good.
- **Endpointing is a hardcoded constant**: `MicStream.endSilenceMs = 900` (ms of
  trailing silence that ends an utterance). Not user-configurable. `startThreshold`
  = 0.06 RMS onset; `setSensitivity(v)` maps 0..1 → 0.12..0.02.
- Echo-aware barge-in VAD: while TTS plays, subtract the output envelope
  (`outputProbe`, `echoK=1.6`), require net signal to hold for `bargeSustainMs=180`
  above `startThreshold*1.8`.
- `detectCaps()` probes the server for stt/tts availability; if `probeServer()`
  fails, `server.tts.available` is false → the Speech "Local" option renders as
  "Local (off)" and the Voice dropdown disables. **This is the "disabled" the
  operator saw — a caps-detection/plumbing issue, not an intentional lock.** The
  `Segmented` primitive has no disabled state at all.

**Reply generation** — `backend/routes/chat.py`, `backend/services/chat_context.py`
- Voice reply reuses the *typed-chat* path: `useVoiceSession.sendTurn` →
  `chatApi.sendMessage` → `POST /api/chat/threads/{id}/messages` (the **blocking,
  non-streaming** endpoint).
- The `general` provider (what a plain voice context resolves to) is already
  **Haiku** (`default_model = TIER_HAIKU`), **no MCP servers**, `max_turns=4`,
  text-only (`chat_context.py:452`). So model tier is NOT the bottleneck.
- Each turn spawns a **fresh `claude -p` subprocess** and boots it cold.
  Persistent `--resume` sessions exist **only for Build agents**
  (`chat.py:413-432`) — general/voice chat gets none.
- `CHAT_TIMEOUT_S = 300`. The call **blocks for the whole reply** before returning;
  voice can't speak until the entire message is generated.
- Streaming already exists but **only for Build mode**: `backend/services/
  build_streaming.py` runs `claude -p --output-format stream-json --verbose` and
  emits SSE. Voice does not use it.

### Diagnosis of "slow / usually no response"
Two compounding latency sources, neither is the model tier:
1. **Cold subprocess every turn** — no warm/persistent session for voice. Spawn +
   MCP connect + model boot on every utterance.
2. **Full-reply block** — voice waits for the entire Haiku completion before the
   first phoneme. No token streaming, no speak-as-it-generates.
Add the 300s ceiling and a tight voice latency budget, and a normal cold turn
*feels* like a failure. Fixing (1) and (2) is likely the biggest single win.

---

## 2. Goals for v2 (what the design must deliver)

**A. Latency + reliability of the reply**
- Warm/persistent session for the voice context (reuse the `--resume` machinery,
  or a resident long-lived worker) so turns aren't cold.
- Stream tokens; start TTS on the first complete sentence (sentence-level
  pipelining already half-exists via `splitSentences` + per-sentence speak).
- Clear failure/timeout UX: never silent. Retry, surface `snapshot.error`.

**B. The interruption / turn system (the heart of the ask)**
- Formalize the layers: *hearing* (VAD/STT), *endpointing* (deciding the user is
  done), *thinking* (LLM), *speaking* (TTS), and a *background* layer that can
  think/prefetch between and during turns.
- **Configurable endpointing delay**: a setting for silence-before-turn (maps to
  `endSilenceMs`). Expose sensible range + default.
- **Speculative endpointing (the operator's "get fancier" idea)**: on the first
  pause, fire an *early tentative* turn to a fast model; if the user resumes
  before it lands, cancel/replace it and re-arm; if silence confirms, commit.
  Goal: fast responses without cutting the user off. Research the known patterns
  (below) — don't invent from scratch.
- Keep + extend heard-tracking and the background-thread queue.

**C. Variable input/output modes**
- Independent **input** (voice | text) and **output** (voice | text) — 2×2.
- The key use case: **listen to the reply, type the responses**, with the same
  interrupt cadence as speaking.
- Interaction rules (operator's starting point, make configurable):
  - Typing does NOT interrupt ongoing speech (you can type while it talks).
  - `Esc` = interrupt/stop speaking.
  - `Enter` = send (and interrupts speech if the interrupt setting is on).
  - "As long as I'm typing, it waits for me" — typing is a soft turn-hold, the
    analogue of still-speaking.
- Design question to resolve: is this a *mode* of the existing voice surface with
  in/out toggles, or a merge of voice + typed chat into one surface with per-turn
  in/out selection? Recommend one, with UI.

**D. Fix the Speech/Voice settings plumbing** so provider selection actually
reflects caps and is operable (or is honestly hidden when a provider is absent).

---

## 3. Research directions (for the design agent)

Find and cite the known-good solutions; map each to our architecture:
- **Turn detection / endpointing beyond fixed silence**: LiveKit turn-detector
  model, Pipecat "smart-turn", OpenAI Realtime *semantic VAD*, Silero VAD.
  Semantic (is the *utterance* complete?) vs energy-only.
- **Speculative / eager endpointing with rollback**: firing an early response and
  cancelling on resume. Look at full-duplex agent stacks (Pipecat, LiveKit
  Agents), "predictive turn-taking", and how realtime APIs handle interruption +
  input_audio_buffer commit/clear semantics.
- **Barge-in / interruption best practice**: echo cancellation, VAD-gated vs
  push-to-talk, how much heard-context to preserve on interrupt.
- **Streaming TTS pipelining**: sentence/clause chunking, first-audio latency.
- **Multimodal in/out**: how other assistants let you type mid-voice-turn and
  mix modalities; the interaction grammar for it.

Deliverable: `docs/voice-mode/design.md` — a complete, buildable design:
architecture (layers + data flow), the turn/interruption state machine (diagram),
the speculative-endpointing algorithm, the in/out-mode model + full interaction
grammar, settings (names, ranges, defaults, commit semantics), backend changes
(session reuse + streaming), UI/UX (must follow `frontend/CLAUDE.md` design
system: primitives, lucide icons, no emoji, five status kinds), and a phased
build plan with clear seams. Ground every recommendation in the files above.

## 4. Constraints (non-negotiable)
- Single-operator system — no multi-tenant guardrails.
- All-local STT/TTS stays the default; cloud only for the LLM (subscription
  `claude -p`, never `ANTHROPIC_API_KEY`).
- UI obeys `frontend/CLAUDE.md` (design system) and app-nav rules.
- No committed stubs; no em-dashes / emoji in UI copy.
