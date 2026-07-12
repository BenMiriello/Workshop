# Voice Mode v2 — design

Status: **design, ready to build** (2026-07-11). Successor to `00-overview.md` (v1 concept)
and `next-gen-brief.md` (the starter brief; goals and diagnosis live there, not restated).
Every recommendation below is grounded in the current code with file:line refs, and in the
researched known-good patterns (section 9, with sources).

Terminology used throughout:

- **utterance** — one contiguous stretch of user speech ending at an endpoint.
- **endpoint** — the moment we decide the user is done and commit the turn.
- **eager pause** — an earlier, lower-confidence pause where we may start work speculatively.
- **draft** — a speculative model turn fired at an eager pause, held silent until commit.
- **worker** — the resident `claude -p` subprocess that generates replies for a voice session.

---

## 1. Architecture and layers

Five layers. The first four are per-turn; the background layer runs between and during turns.

| Layer | What it does | Lives where |
|---|---|---|
| **Hearing** | mic capture, VAD, level metering, echo-aware barge detection, STT | client (`voice.ts` MicStream, lines 93-224) + backend `/api/voice/stt` |
| **Endpointing** | decides the user is done: silence timer + eager pause + typing hold + completeness heuristic | client (`voiceSession.ts`, new logic; MicStream gains a second timer) |
| **Thinking** | LLM reply generation, streamed | backend: resident worker (`services/voice_worker.py`, NEW) + SSE turn stream |
| **Speaking** | sentence-pipelined TTS with heard tracking and barge-in | client (`voice.ts` Speakers, extended to an appendable queue) + backend `/api/voice/tts` |
| **Background** | thread queue, speculative drafts, worker prewarm, TTS render-ahead, context refresh | split: queue + drafts + render-ahead on client; prewarm + session state on backend |

Data flow:

```
CLIENT (browser)                                      BACKEND (FastAPI)
+---------------------------------------------+      +------------------------------------------+
| VoiceMode.tsx  (the conversation surface)   |      | routes/voice.py                          |
|   orb / captions / composer / queue / prefs |      |   existing: /health /voices /stt /tts    |
|          | useVoiceSession.ts               |      |             /prefs                       |
|          v                                  |      |   NEW: /session (prewarm)                |
| VoiceSession (voiceSession.ts)              |      |        /session/{k}/turns  (start turn)  |
|   HEARING    MicStream VAD -----------------+--+   |        /turns/{id}/stream  (SSE)         |
|   ENDPOINT   silence + eager + typing hold  |  |   |        /turns/{id}/cancel  (+heard)      |
|   THINKING   SSE consumer, sentence cutter -+--+-->|        /turns/{id}/commit                |
|   SPEAKING   StreamSpeaker (append queue) --+--+   |            |                             |
|   BACKGROUND drafts / queue / prewarm       |  |   | services/voice_worker.py  (NEW)          |
+---------------------------------------------+  |   |   one resident `claude -p` per session   |
        ^  audio wav per sentence                |   |   stream-json stdin/stdout, token deltas |
        +----------------------------------------+   |   idle reaper, crash -> --resume respawn |
           POST /stt (webm)   POST /tts (text)       | services/turn_stream.py  (FACTORED out   |
                                                     |   of build_streaming: Turn registry,     |
                                                     |   SSE iter_events, chat_store persist)   |
                                                     | chat_store (thread history, unchanged)   |
                                                     | voice_engine (faster-whisper + Kokoro)   |
                                                     +------------------------------------------+
```

Interaction **between turns**: the background layer keeps the worker warm (prewarmed on
surface mount, reaped after 10 min idle), holds the offerable thread queue, and refreshes the
live-context snapshot hash so the next turn only pays for context that changed.

Interaction **during a turn**: hearing never stops (mic stays hot through thinking and
speaking, exactly as today, `voiceSession.ts:255-260`); endpointing runs eager evaluation
while the user is still mid-utterance; thinking streams while speaking plays; speaking
render-ahead runs one sentence ahead of playback.

What stays: all of `voice.ts` provider tiers (native > server > browser, `voice.ts:65-80`),
the Speaker classes, `splitSentences`/`cleanForSpeech` (`voice.ts:524-549`), the prefs
store, the echo-aware barge VAD (`voice.ts:107-127,147-187`). What is removed: the blocking
`sendTurn` reply path (`useVoiceSession.ts:22-34` -> `chat.py:375` POST /messages) as the
primary path (it remains as fallback only), and the "Muted" option inside the provider
segmented control (replaced by a dedicated output toggle, section 5).

---

## 2. Turn + interruption state machine

Extend `voiceSession.ts`, do not replace. The five public states stay
(`voiceSession.ts:38`); UI code keyed on them (`VoiceMode.tsx:16-30`) keeps working. Two
additions: internal flags on `listening` (draft in flight, typing hold) and a `speaking`
that can begin while generation is still streaming.

```
                        tap orb / auto-start
  idle ── start() ──> connecting ──caps+mic ok──> LISTENING <───────────────────────────┐
                                                    │  ▲  ▲                              │
                     eager pause (eager_silence_ms) │  │  │ user resumes speaking        │
                     -> cut segment, STT, fire draft│  │  │ -> cancel draft (superseded) │
                                                    ▼  │  │                              │
                                              LISTENING+draft ──────────────────────────│
                                                    │                                    │
              endpoint (endpoint_silence_ms + completeness gate + no typing hold)        │
                                                    │                                    │
             draft matches final text ──────────────┼──── no draft / mismatch            │
                     │                              │           │                        │
                     ▼                              ▼           ▼                        │
              commit draft                      THINKING (start committed turn)          │
              (already generating,                  │                                    │
               audio pre-rendered)                  │ first sentence audio ready         │
                     └──────────────┬───────────────┘                                    │
                                    ▼                                                    │
                                SPEAKING  (generation may still be streaming)            │
                                    │  │                                                 │
                     reply finishes │  │ BARGE-IN: sustained speech (voice.ts:158-168),  │
                     and queue idle │  │ or Esc, or Enter-send with enter_interrupts     │
                                    │  ▼                                                 │
                                    │ stop at sentence boundary, cancel generation,      │
                                    │ queue unspoken remainder, report heard count ──────┤
                                    └────────────────────────────────────────────────────┘
  stop() from any state -> idle (worker kept warm server-side until idle reap)
```

Events (new ones marked +):

| Event | Source | Handled in state |
|---|---|---|
| `speechStart` | MicStream VAD onset | listening (marks resume, cancels draft), speaking (barge-in, `voiceSession.ts:255-260`) |
| + `eagerPause` | MicStream, net silence >= `eager_silence_ms` | listening (fire draft, section 4) |
| `speechEnd` (endpoint) | MicStream, net silence >= `endpoint_silence_ms` | listening (commit turn, `voiceSession.ts:262-279` extended) |
| + `typingHold` / `typingRelease` | composer keystrokes / draft emptied + hold window elapsed | listening (defers endpoint), speaking (no effect) |
| + `turnDelta` | SSE `text` events | thinking/speaking (grow `sentences[]`) |
| + `turnDone` / `turnError` | SSE `done`/`error` | any (error -> retry once, surface `snapshot.error`) |
| `bargeIn` | speechStart-while-speaking, Esc, orb tap, Enter(+interrupt) | speaking (`voiceSession.ts:355-361`) |
| + `submitText` | Enter in composer | listening/speaking (section 5 grammar) |

### Heard tracking: exact definition (kept, extended)

Today's model is right and stays: the reply is split into sentences, each
`pending | speaking | heard` (`voiceSession.ts:40-41`). A sentence is **heard** only when
its audio finished playing (`onSentenceEnd`, `voiceSession.ts:339-343`). The sentence in
flight when interrupted is **not** heard: `ServerSpeaker.stop()` finishes with
`reached = this.idx`, the index still playing (`voice.ts:399-403`), and `finishSpeaking`
slices the remainder from there (`voiceSession.ts:371-378`).

v2 extends it for streaming:

- `sentences[]` **grows during the turn** as deltas arrive (status `pending`), instead of
  being fixed up front (`voiceSession.ts:316-317`). The caption already renders
  pending/now/heard styling (`VoiceMode.tsx:169-181`); it just fills in progressively.
- On interrupt, the queued remainder is **generated-but-unspoken sentences only**. The
  not-yet-generated tail is cancelled at the worker (we also send `interrupt`, section 3),
  so it never exists; the queue never contains text the model did not actually produce.
- **The model is told what landed.** This is the local analogue of OpenAI Realtime's
  `conversation.item.truncate`, which exists precisely to keep the model's context matched
  to what the user actually heard. Client reports `heard_count` on cancel
  (`POST /turns/{id}/cancel {heard: n}`); it is persisted on the assistant message meta
  (`{heard: n, total: m}`), and the next user message to the worker is prefixed:
  `[note: you were interrupted; the last sentence I heard was: "<sentence n>". Anything after that was not heard.]`
  The voice persona explains this convention once, in the system prompt.
- Asides keep working: the `⟦aside:…⟧` extraction (`voiceSession.ts:87,304-311`) runs on
  each completed sentence before it is enqueued for TTS, not on the whole reply.

---

## 3. Latency plan

Diagnosis recap (brief section 1): every voice turn cold-spawns `claude -p` and blocks for
the full completion (`useVoiceSession.ts:30` -> `chat.py:465-471`, `CHAT_TIMEOUT_S = 300`
at `chat.py:68`). Persistent sessions exist only for Build agents (`chat.py:429-448`);
streaming exists only for Build turns (`chat.py:535-615`, guard at `chat.py:545-546`).

### 3a. Warm session: a resident worker (recommended) over per-turn `--resume`

Two candidate fixes were on the table:

1. **Reuse `--resume` for voice threads** (the Build-agent pattern,
   `llm_router.py:425-428`). Kills the prompt-rebuild and history-resend, but every turn
   still pays a fresh CLI process: node boot, session load from disk, auth. That is a
   large fraction of the observed cold-turn cost and it never goes away.
2. **A resident worker**: one long-lived `claude -p --input-format stream-json` process per
   voice session, fed user messages over stdin, streaming replies over stdout.

**Recommendation: the resident worker.** The CLI supports it natively (verified locally:
`claude --help` lists `--input-format <format>` "only works with --print" and
`--include-partial-messages`), it eliminates process spawn AND gives token streaming in the
same move, and it makes the model session cache-stable across turns (consistent with the
locked agent-chat-redesign decision on cache-stable context). `--resume` machinery is not
wasted: it becomes the crash-recovery path.

`backend/services/voice_worker.py` (NEW):

```python
# One worker per voice session key (thread id). Single-operator system: expect one,
# tolerate a few (e.g. desktop + phone). No pooling machinery.
cmd = [
    CLAUDE_CLI, "-p",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--include-partial-messages",       # token-level deltas, not whole-message events
    "--verbose",
    "--model", model,                    # provider default: TIER_HAIKU (chat_context.py:452)
    "--session-id", session_uuid,        # so a crashed worker respawns with --resume
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--tools", "",                       # voice = general provider = text-only (chat_context.py:443-445)
    "--append-system-prompt", voice_persona,   # persona + brevity + interruption/supersede conventions
]
env = llm_router._claude_env()           # subscription OAuth only, never ANTHROPIC_API_KEY
```

Notes on the flags: no `--max-turns` (tools are off, there is no agentic loop to bound;
verify in Phase 1 that omitting it does not cap the number of stdin turns). Persona goes in
`--append-system-prompt` so it is sent once and stays cache-stable; per-turn dynamic
context (the general provider's finance/commute/location snapshot,
`chat_context.py:464-481`) is injected as a prefix on the **first** user message and then
only re-sent as a `[context update]` block when its hash changes. Turn messages on stdin:

```json
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"<prefix + utterance>"}]}}
```

Lifecycle:

- **Prewarm**: `POST /api/voice/session {context_key}` on surface mount (before the first
  utterance) spawns the worker and creates/binds the thread. By the time the user finishes
  the first sentence the process is up. This replaces most of the value of the
  `speakFiller("One moment.")` cold-start hack (`voiceSession.ts:290`), which stays as a
  last resort for genuinely cold turns.
- **Idle reap**: worker killed after `VOICE_WORKER_IDLE_S = 600` with no turn; the
  `--session-id` means the next `POST /session` respawns with `--resume <id>` and full
  conversational memory (reuse `build_streaming.session_alive` at
  `build_streaming.py:35-51` to validate before resuming).
- **Crash / wedge**: a returncode watcher marks the session cold; the in-flight turn gets
  an SSE `error`; the client retries once, and the retry path falls back to the existing
  stateless `call_cloud` route (`chat.py:375`) while the worker respawns. Voice never goes
  fully dark because the worker died.
- **Interrupt** (used by barge-in and speculative cancel): write a control request to
  stdin, the same mechanism the Agent SDK uses in streaming-input mode:
  `{"type":"control_request","request_id":"...","request":{"subtype":"interrupt"}}`.
  This must be verified in Phase 2; the documented fallback is kill + respawn with
  `--resume` (costs one spawn, only on interrupt, still correct).

Turn plumbing reuses what Build streaming already proved out: factor `Turn`, `TURNS`,
`_push`, `iter_events`, the SSE framing, and the incremental chat_store persistence
(`append_streaming_assistant` / `update_streaming_assistant` / `finalize_streaming_assistant`,
`build_streaming.py:54-95,208-252,433-452`) out of `build_streaming.py` into a shared
`services/turn_stream.py`. `build_streaming` keeps its subprocess-per-turn runner;
`voice_worker` feeds the same Turn registry from the resident process's stdout. The
`STREAM_LINE_LIMIT` lesson (`build_streaming.py:28-32`) carries over.

New routes (in `routes/voice.py`, keeping voice concerns out of `chat.py`):

| Route | Does |
|---|---|
| `POST /api/voice/session` | prewarm/bind worker for `{context_key, thread_id?}`; returns `{session_key, thread_id}` |
| `POST /api/voice/session/{key}/turns` | `{text, speculative}` -> `{turn_id}`; writes stdin message; speculative turns **defer** chat_store writes (section 4) |
| `GET /api/voice/turns/{turn_id}/stream` | SSE, identical framing to `chat.py:618-651` (`id:` offsets, Last-Event-ID resume) |
| `POST /api/voice/turns/{turn_id}/cancel` | `{reason: "superseded"|"barge", heard?: n}` -> interrupt worker, drop or finalize persistence, record heard meta |
| `POST /api/voice/turns/{turn_id}/commit` | promote a speculative turn: flush deferred chat_store writes; client releases the audio hold |
| `DELETE /api/voice/session/{key}` | explicit teardown on `session.stop()` (reaper covers the rest) |

Voice turn timeout: **60 s** (a voice reply that takes longer is a failure regardless);
independent of `CHAT_TIMEOUT_S = 300` which stays for typed chat.

### 3b. Token streaming with sentence-level TTS pipelining

Client side, three changes:

1. **Streaming turn client** in `useVoiceSession.ts`: replace the blocking
   `chatApi.sendMessage` (`useVoiceSession.ts:30`) with `POST .../turns` + an
   `EventSource` on the turn stream, feeding `session.onReplyDelta(text)` /
   `onReplyDone()` / `onReplyError()`. Same event kinds the Build stream already emits
   (`text`, `result`/`done`, `error`).
2. **Incremental sentence cutter** in `voiceSession.ts`: buffer deltas; run
   `splitSentences(cleanForSpeech(buffer))` (`voice.ts:524-549`) and emit every complete
   sentence except the trailing fragment (it may still be growing); flush the fragment on
   `onReplyDone`. The existing min-24-char merge rule (`voice.ts:528`) already prevents
   choppy fragments, matching the industry guidance to chunk at sentence boundaries and
   never mid-sentence.
3. **Appendable Speaker**. Extend the `Speaker` interface (`voice.ts:283-288`) with
   `enqueue(sentence: string)` and `end()`; `speak(sentences, ev)` becomes
   `begin(ev) + enqueue(...) + end()`. And fix the render gap: `ServerSpeaker`'s header
   comment claims it "prefetches the next sentence's audio" (`voice.ts:291-292`) but
   `playFrom(i)` awaits `render()` before every sentence (`voice.ts:363-388`), so there is
   a full Kokoro round-trip of dead air between sentences. v2 keeps a rolling render-ahead
   of **2 sentences**: when sentence `i` starts playing, `render(i+1)` (and `i+2` if
   queued) are already in flight. Browser/Native speakers implement `enqueue` trivially
   (their per-sentence engines are already sequential, `voice.ts:421-458,484-509`).

### First-audio latency target

Measured from **endpoint commit** to **first TTS audio sample** (the number the operator
feels). Budget on this box (24-core CPU, all local except the LLM):

| Stage | Today (cold, blocking) | v2 warm | v2 + speculation (section 4) |
|---|---|---|---|
| STT (faster-whisper base.en, short utterance) | 300-500 ms | 250-400 ms | ~0 (overlapped with the pause) |
| LLM: process spawn | 1.5-3 s | 0 (resident) | 0 |
| LLM: time-to-first-sentence (Haiku) | full completion, 3-15 s | 600-1200 ms | ~0-400 ms (draft already streaming) |
| TTS first sentence (Kokoro CPU) | 200-500 ms | 200-500 ms | ~0 (pre-rendered during hold) |
| **Endpoint -> first audio** | **5-20 s (feels broken)** | **p50 <= 1.5 s, p90 <= 2.5 s** | **p50 <= 0.6 s** |

Instrument it: the session records a per-turn timeline (`t_endpoint, t_stt_done, t_sent,
t_first_delta, t_first_sentence, t_first_audio`) and the footer shows the last turn's
endpoint-to-audio number (section 7). Phase 1 is verified against these marks, not vibes.

Failure UX (goal A of the brief, "never silent"): SSE `error` or 60 s timeout ->
`snapshot.error` set (already rendered, `VoiceMode.tsx:125-129`), one automatic retry
(safe: the user message persists exactly once, on commit), and if `speak_replies` is on,
a short spoken notice ("That one failed. Trying again.") so eyes-free use is never left
hanging. StatusPill shows `warn` while degraded (fallback path or browser-tier providers).

---

## 4. Speculative endpointing algorithm

Pattern match: this is Deepgram Flux's eager end-of-turn model
(`EagerEndOfTurn` -> draft, `TurnResumed` -> discard, `EndOfTurn` -> finalize) combined
with LiveKit's preemptive generation defaults (generation eager, TTS not eager:
`preemptive_generation.enabled=true`, `preemptive_tts=false`). We implement both levels
and default like LiveKit: **think eagerly, speak on confirmation**.

Where it runs: the client owns the triggers (it owns the VAD); the backend supplies STT
and the draft turn. Applies to the **server STT path only**: browser Web Speech and native
STT self-endpoint (`voiceSession.ts:264-265`), so they cannot give us an eager pause; on
those providers the feature is simply inert (settings hidden, section 6).

Mechanics of the eager cut: MediaRecorder cannot be tapped mid-flight, so an eager pause
**cuts a segment** (`stopRecording()` -> blob, then immediately `startRecording()` again,
`voice.ts:189-209`). An utterance becomes 1..N segments; each is transcribed independently
and the texts are joined with a space. The seam always falls inside a real pause
(>= `eager_silence_ms` of net silence), so mid-word cuts cannot happen.

```
// per listening phase, in voiceSession.ts
segs: string[] = []            // transcribed segments of the utterance so far
draft: { turnId, sentAt, text } | null = null
eagerFires = 0                 // cap per utterance
resumedSinceCut = false

onSpeechStart():                       // VAD onset (voice.ts loop)
  if state == "speaking" and prefs.barge_in: bargeIn(); return
  resumedSinceCut = true
  if draft:                            // user kept talking: the draft answered a fragment
    POST /turns/{draft.turnId}/cancel {reason:"superseded"}   // interrupts generation,
    draft = null                       // drops its deferred chat_store writes

onEagerPause():                        // net silence >= eager_silence_ms, state == listening
  if not prefs.eager_endpoint: return
  if eagerFires >= 2: return           // LiveKit caps retries (max_retries=3); 2 is enough
  eagerFires += 1
  resumedSinceCut = false
  blob = await mic.cutSegment()
  text = await transcribeBlob(blob)    // 200-400 ms; the confirm window absorbs it
  if text: segs.push(text)
  if resumedSinceCut: return           // spoke during STT: keep the segment, no draft
  if typingHoldActive(): return        // typing means "wait for me" (section 5)
  if not incomplete(join(segs)):       // completeness gate, below
    draft = await startVoiceTurn(join(segs), speculative=true)
    // draft SSE is consumed into the normal sentence pipeline, but:
    //  - sentences are TTS-rendered (render-ahead) and HELD, not played
    //  - if prefs.eager_speak: play immediately (level 2, default off)

onEndpoint():                          // net silence >= endpoint_silence_ms
  if typingHoldActive(): return        // endpoint re-fires when the hold releases
  tail = await mic.cutSegment()
  if tail and tail.size >= 1200:       // same floor as today (voiceSession.ts:269)
    t = await transcribeBlob(tail); if t: segs.push(t)
  final = join(segs); reset(segs, eagerFires)
  if not final: rearm listening; return
  if incomplete(final) and extensions < 1:
    extendEndpointOnce(); return       // completeness gate, below
  if draft and draft.text == final:
    POST /turns/{draft.turnId}/commit  // flush persistence
    releaseAudioHold()                 // pre-rendered sentences start playing NOW
  else:
    if draft: cancel(draft, "superseded")
    startVoiceTurn(final, speculative=false)
```

**Completeness gate** (the cheap semantic layer, modeled on OpenAI semantic VAD's
"trailing 'ummm' lowers end-probability, so wait longer" and LiveKit's
min/max endpointing delays): a pure-text heuristic on the transcript so far.
`incomplete(text)` is true when the tail matches a continuation pattern:
ends in `,;:` or an open bracket/quote, or the last word is in a small conjunction/filler
set (`and but or so because although which um uh er like then if when...`).
Effect: at an eager pause it suppresses the draft (do not think about half a thought);
at the endpoint it extends the wait **once** by `min(endpoint_silence_ms, 1200) ms`,
bounded like LiveKit's `max_delay` so a trailing "and..." can never wedge the turn open
for more than roughly double the configured delay. No model, no cost, runs in microseconds;
the ML upgrade slot is Phase 6 (smart-turn v3, section 9).

**Races and double-work, explicitly:**

- At most **one draft in flight** ever; `eagerFires <= 2` per utterance bounds waste.
- `resumedSinceCut` is checked after the STT await, so speech that started while STT ran
  can never race a draft into existence.
- The user message and assistant placeholder for a **speculative** turn are NOT written to
  `chat_store` at start (unlike Build turns, `build_streaming.py:208-212`); they are
  buffered in the Turn record and flushed on `/commit` (or dropped on cancel). Thread
  history therefore never shows phantom turns. This is the one behavioral difference from
  the shared `turn_stream` path, carried as a `deferred_persist` flag on the Turn.
- **Session pollution on cancel** (the cost of a resident worker): a superseded draft has
  already put a user fragment + partial reply into the model session. We interrupt the
  generation (bounding wasted tokens) and the committed turn's message carries a marker:
  `[correction: my previous message was an incomplete fragment of this one and your reply to it was never spoken. Answer this full message fresh.] <final text>`
  The persona explains the convention. This is strictly better than the alternative
  (running drafts on a separate stateless subprocess), which would re-pay the cold spawn
  the whole design exists to remove. Deepgram sidesteps this only because it guarantees
  the eager and final transcripts match; we cannot, so we say so to the model.
- **Cost**: Deepgram's published estimate for this pattern is 50-70% more LLM calls to
  save 100-200 ms; ours saves more (it overlaps STT + TTFT + first-sentence TTS, roughly
  0.9-1.5 s) because our confirm window is longer and our stack is local. On subscription
  Haiku with `--max-turns`-less text-only turns, the marginal cost is token burn, capped
  by the 2-fires rule and by interrupts landing within ~1 s of cancel.

**Level 2, `eager_speak`** (default off): commit-at-eager-pause; audio starts at the eager
pause instead of the endpoint. If the user resumes while it is speaking, that is a false
start, not a real barge-in: stop audio instantly, cancel as superseded, do NOT queue the
remainder (the reply was to a fragment; queueing it would offer garbage). Mirrors LiveKit's
`false_interruption_timeout` + `resume_false_interruption` semantics, inverted for the
agent's own false start.

---

## 5. Variable input/output modes

### The design question, resolved

Brief question: is mixed in/out a *mode of the voice surface*, or a *merge of voice and
typed chat into one surface*? **Recommendation: evolve the voice surface into the
modality-flexible conversation surface; do not merge it into ChatPanel in v2.**

Reasons, in order:

1. The locked agent-chat-redesign plan (`docs/agent-chat-redesign/plan.md:42,119-124`)
   already decides the end state: modality is a per-thread option, and its "voice small
   step" is a chat-toolbar mic toggle that mounts **the same VoiceSession** in place.
   Keeping VoiceSession the single engine (framework-agnostic, `voiceSession.ts:18`) and
   teaching *it* the modality flags means the eventual chat-side toggle is a mounting
   detail, not a rewrite. Industry went the same way (ChatGPT retired its separate
   voice-only interface in Nov 2025 in favor of voice-inside-chat), so the merged end
   state is right; the sequencing through the shared engine is what makes it cheap.
2. A merge-now would drag the full ChatPanel feature set (segments, tool chips, markdown,
   SSE reconnect, drafts) into the latency-critical voice loop and stall the actual
   latency work. The voice surface with a composer delivers the operator's stated use case
   (listen to replies, type responses, same interrupt cadence) with ~1 component.
3. Everything lands in the same thread via `chat_store` either way; a conversation started
   by voice is already readable and continuable in ChatPanel today. The surfaces share
   data, so keeping two of them costs nothing in state.

### The mode model: two independent toggles, not four exclusive modes

The honest 2x2 is: text-in always exists (composer is always rendered), text-out always
exists (captions are always rendered; they are also the heard-tracking display). So the
only real switches are the two voice lanes:

- **Mic** (voice input): on/off. Off = VAD and capture stop; endpointing idles; the
  composer is the only input. Maps to a new pref `mic_enabled`.
- **Speak replies** (voice output): on/off. Off = replies render as streaming captions
  only, marked all-heard immediately (the muted path that exists at
  `voiceSession.ts:319-325`). Maps to a new pref `speak_replies`, replacing the "Muted"
  option currently overloaded onto `tts_provider` (`voiceSession.ts:48,205` and
  `VoiceMode.tsx:114`); `tts_provider` goes back to being purely an engine choice.

All four combinations work and mean what you expect; "listen to replies, type responses"
is Mic off + Speak replies on.

### Interaction grammar (all rules configurable; settings in section 6)

| Input | While idle/listening | While thinking | While speaking |
|---|---|---|---|
| **Speaking** (mic on) | normal utterance flow | barge on thinking: allowed, cancels turn (rare; VAD gated) | barge-in (existing, `voiceSession.ts:255-260`) |
| **Typing** (any keystroke into composer) | soft turn-hold: endpoint deferred while draft text exists or last keystroke < `typing_hold_ms` ago; mic keeps capturing | no effect | **no effect on playback** (operator rule: typing never interrupts speech) |
| **Enter** | send composer + any pending voice segments as one turn | supersedes the in-flight turn (cancel + resend combined text) | if `enter_interrupts`: barge-in (remainder queued) then send; else: send is held until speaking finishes, composer shows a "queued" Badge |
| **Shift+Enter** | newline in composer | newline | newline |
| **Esc** | clears typing hold (utterance endpoint can fire) | cancels the turn | interrupt: stop at sentence boundary, queue remainder, cancel generation (never sends anything) |
| **Orb tap** | force endpoint now (`forceEndUtterance`, `voiceSession.ts:408-411`) | (no-op) | interrupt (same as Esc) |

The one merge rule that needs stating: if the mic captured segments this utterance AND the
composer has text, Enter sends **both**, transcript first, as one turn. The pending
transcript is visible as ghost text above the composer the whole time, so nothing is sent
unseen. (Rationale: one pending-turn buffer is the only model where "typing is the
analogue of still-speaking" holds together.)

Typing hold is the typed twin of the completeness gate: "as long as I'm typing, it waits
for me". It also suppresses eager drafts (section 4 pseudocode) because a draft fired
mid-typing would answer half the input.

---

## 6. Settings

All of these are interaction/view preferences (no config or money writes), so per the
design-system commit table (`frontend/CLAUDE.md`, "When does a control commit?") every one
**commits on change**, through the existing debounced persist
(`updatePrefs` -> `voiceApi.putPrefs`, 400 ms, `useVoiceSession.ts:79-88`). No ActionBar,
no Save. `VoicePrefs` (`voiceSession.ts:45-63`) grows accordingly; the backend prefs store
is already schemaless JSON.

Quick controls (always visible on the surface, not inside the settings sheet):

| Setting | Pref key | Control | Range | Default | Notes |
|---|---|---|---|---|---|
| Mic | `mic_enabled` | icon Toggle (Mic / MicOff, lucide) | on/off | on | voice input lane |
| Speak replies | `speak_replies` | icon Toggle (Volume2 / VolumeX) | on/off | on | voice output lane; replaces "Muted" |

Settings sheet:

| Setting | Pref key | Control | Range / options | Default | Notes |
|---|---|---|---|---|---|
| Voice | `voice` | `Select` | server voice list | `af_heart` | Skeleton while loading; hidden when engine is browser/native |
| Speed | `speed` | range slider | 0.7-1.4, step 0.05 | 1.0 | applies to all engines |
| Speech engine | `tts_provider` | `Segmented` | Auto / Local / Browser | auto | honest caps, below; "Muted" removed |
| Hearing engine | `stt_provider` | `Segmented` | Auto / Local / Browser (+ Device when native) | auto | **new in UI**; pref exists (`voiceSession.ts:49`) but was never surfaced |
| Pause before reply | `endpoint_silence_ms` | range slider, ms readout | 300-2000, step 50 | 900 | maps to `MicStream.endSilenceMs` (`voice.ts:103`), today a hardcoded constant; server STT path only, hidden otherwise |
| Think during pauses | `eager_endpoint` | `Toggle` | on/off | on | speculative drafts (section 4); server STT path only |
| Eager pause | `eager_silence_ms` | range slider | 200-800, step 50 | 400 | shown only when eager on; clamped < `endpoint_silence_ms` - 200 |
| Reply during pauses | `eager_speak` | `Toggle` | on/off | off | level 2; shown only when eager on |
| Let me interrupt | `barge_in` | `Toggle` | on/off | on | existing |
| Interrupt sensitivity | `barge_sensitivity` | range slider | 0-1, step 0.05 | 0.5 | existing (`voice.ts:118-121`) |
| Typing holds my turn | `typing_hold` | `Toggle` | on/off | on | section 5 |
| Hold window | `typing_hold_ms` | range slider | 1000-10000, step 500 | 3000 | shown only when hold on |
| Enter interrupts speech | `enter_interrupts` | `Toggle` | on/off | on | section 5 grammar |
| Reply length | `verbosity` | `Segmented` | Terse / Normal | normal | pref exists (`voiceSession.ts:52`), never surfaced; feeds the persona |

### Fixing the Speech/Voice provider plumbing (goal D)

What the operator saw: with the voice server down/unprobed, `serverTts` is falsy, so the
Voice `Select` hard-disables (`VoiceMode.tsx:89`) and the Speech segmented shows
"Local (off)" as a normal-looking but dead option (`VoiceMode.tsx:112`); `Segmented` has no
disabled affordance at all, and `caps` only populates after `session.start()`
(`voiceSession.ts:166`), so before the first start everything looks broken. Fixes:

1. **Probe on mount, not on start**: `detectCaps()` (`voice.ts:65-80`) runs when the
   surface mounts and again when the settings sheet opens; result stored on the session so
   `resolveProviders` (`voiceSession.ts:197-209`) and the UI share one truth.
2. **`Segmented` gains per-option `disabled`** (primitive change in `ui/Segmented.tsx` +
   a `.ws-` rule): dimmed, non-interactive, `cursor: default` (never `not-allowed`, DS
   rule 10), with an option-level `hint` rendered as a caption under the control when a
   disabled option exists ("Voice server offline"). This is a general primitive upgrade,
   not a voice one-off (reusable-components rule).
3. **Auto shows its resolution**: label renders as "Auto (Local)" / "Auto (Browser)" from
   the resolved provider, so auto is never a mystery. The footer keeps the live truth
   (`hear: server / speak: server`, `VoiceMode.tsx:215-217`).
4. **Recovery**: choosing a provider that is down falls back at resolve time exactly as
   today (`voiceSession.ts:197-209`); the difference is the UI now shows why.

---

## 7. UI/UX

One screen, evolving `VoiceMode.tsx` in place. Everything from `src/ui` primitives + new
`.ws-voice-*` rules in `components.css`; lucide only; no emoji; no inline hex; copy uses
no em-dashes.

```
+----------------------------------------------------------------------+
| [< Back]         (AudioLines) context-key      [StatusPill] [gear]   |   ws-voice-top
+----------------------------------------------------------------------+
|  settings sheet (when open): the table in section 6, grouped under   |
|  SectionLabels: Voice, Turn taking, Typing, Engines                  |
+----------------------------------------------------------------------+
|                                                                      |
|                          [   the orb   ]                             |   ws-voice-stage
|                    state icon + level halo                           |
|                 hint line under the orb (one line)                   |
|                                                                      |
+----------------------------------------------------------------------+
|  transcript region (scrolls, newest pinned):                         |   ws-voice-transcript
|    You: ...                                                          |
|    Clawd: heard heard heard | speaking | pending pending             |
+----------------------------------------------------------------------+
|  queue chips: "Threads I haven't finished"  [topic  > play  x drop]  |   ws-voice-queue
+----------------------------------------------------------------------+
|  ghost text: pending voice transcript (dimmed, live)                 |
|  [ composer textarea, autogrow                    ] [Send]           |   ws-voice-composer
|  [Mic toggle] [Speak toggle]      last turn: 0.9s to speech          |   ws-voice-footer
|  [End session]                    hear: server  speak: server        |
+----------------------------------------------------------------------+
```

Components and their `src/ui` sources:

- **Top bar**: unchanged (`Button` ghost, `StatusPill`, `Settings2` icon button,
  `VoiceMode.tsx:66-80`). StatusPill mapping change: `thinking` moves from `warn` to
  `running` (`VoiceMode.tsx:24-30`); it is active work, and `running` is the one animated
  kind (DS rule 3). `warn` is reserved for degraded states: fallback provider in use,
  retrying a failed turn.
- **Orb**: existing button + level halo (`VoiceMode.tsx:131-147`). New sub-cue: while a
  draft is in flight during listening, the halo gets a slow secondary pulse
  (reduced-motion: static ring). No new state name is shown; the user-facing state is
  still "Listening".
- **Transcript**: existing heard/now/pending caption spans (`VoiceMode.tsx:169-181`),
  now filling progressively as deltas arrive. Keeps the last 12 turns in a scrollable
  region (overflow state); older history lives in the thread, one tap away in chat.
- **Composer** (new): `Textarea` (autogrow, 1-4 rows) + `Button` with `SendHorizonal`
  icon; ghost transcript line above it; a `Badge` "queued" on the send button when a send
  is held for end-of-speech (section 5). Keyboard handling per the grammar table. Mirrors
  the chat composer's behavior; if extraction to a shared primitive is cheap when built,
  do it (reusable-components rule), otherwise keep local and consistent.
- **Quick toggles**: `Toggle` pair with lucide `Mic`/`MicOff`, `Volume2`/`VolumeX` labels
  in the footer row.
- **Queue**: unchanged chips (`VoiceMode.tsx:187-208`).

Four states (DS rule 9):

- **loading**: `connecting` orb state + `Skeleton` rows in the settings sheet while
  `detectCaps` and `/voices` resolve.
- **empty**: idle stage hint "Tap the orb and talk, or type below." (composer works even
  before the mic ever starts).
- **error**: `StateBlock variant="error"` (already wired, `VoiceMode.tsx:125-129`) naming
  the failing thing (`stt: ...`, `reply: ...`) plus a Retry button that re-fires the last
  turn; mic failures on the server path keep today's fail-closed behavior
  (`voiceSession.ts:175-184`).
- **overflow**: transcript region scrolls; queue caps visible chips at 5 with a
  "+N more" expander.

Latency readout: the footer shows `last turn: <t_endpoint -> t_first_audio>s to speech`
(from the section 3 timeline marks). It is the permanent, honest instrument for the whole
point of v2; not a debug flag.

Accessibility: all controls are the primitives, so focus rings and Esc handling come free;
Esc is intercepted for interrupt **only** when the settings sheet/modal is closed (Modal
retains its own Esc-to-close); orb keeps its aria-labels (`VoiceMode.tsx:136`);
reduced-motion collapses halo pulses to static rings.

---

## 8. Phased build plan

Each phase ships alone and leaves the system working; no committed stubs; order is
smallest-valuable-first with the latency core right behind the one-day opener.

**Phase 0 — settings + endpointing delay + honest caps** (small, directly requested)
- `endpoint_silence_ms` pref end-to-end: prefs schema, slider, `MicStream.endSilenceMs`
  setter (today constant at `voice.ts:103`), applied live via `setPrefs`.
- `stt_provider` control surfaced; `verbosity` surfaced; caps probe on mount; `Segmented`
  per-option disabled + hint; "Auto (X)" resolution labels; "Muted" replaced by the
  `speak_replies` quick toggle.
- Verify: slider changes audibly move the pause; stop the voice server -> options dim with
  the hint, browser tier still works; `check_design.sh` + `tsc --noEmit` pass.
- Risk: none structural. This also delivers the operator's two explicit small asks first.

**Phase 1 — the latency core: resident worker + streaming + sentence pipelining** (the big win)
- Backend: factor `turn_stream.py` out of `build_streaming.py` (no behavior change to
  Build; verify a Build chat turn end-to-end after the refactor). Add `voice_worker.py`,
  the five voice routes, prewarm-on-mount, idle reaper, crash -> `--resume` respawn,
  60 s voice turn timeout, stateless `call_cloud` fallback path.
- Frontend: streaming turn client in `useVoiceSession`, incremental sentence cutter,
  appendable `Speaker` + real render-ahead (fixing `voice.ts:363-388`), per-turn latency
  timeline + footer readout, retry-once + spoken failure notice.
- Verify: endpoint-to-first-audio p50 <= 1.5 s warm over 20 turns (footer numbers);
  kill the worker mid-turn -> visible error, retry lands via fallback; second turn
  demonstrates conversational memory (session persistence) without history resend;
  `--input-format stream-json` multi-turn behavior confirmed (including the no
  `--max-turns` assumption).
- Risks: `--include-partial-messages` event shapes (parse defensively like
  `_parse_stream_event`, `build_streaming.py:97-155`); stdin turn framing quirks; both are
  contained in `voice_worker.py`.

**Phase 2 — interruption v2** (make barge-in correct under streaming)
- Barge-in and Esc cancel generation at the worker (`control_request` interrupt; verify,
  else the documented kill+`--resume` fallback), queue the generated-but-unspoken
  remainder, report `heard_count` on cancel, persist heard meta, inject the
  interruption note on the next turn.
- Esc / orb-tap / Enter grammar for interrupt paths (Enter-send still goes to the old
  composer-less flow until Phase 4; Esc and orb work now).
- Verify: interrupt mid-reply -> audio stops within one sentence, queue chip appears with
  the right remainder text, next turn's model reply demonstrably knows where it was cut
  (ask it "what did I hear?"); transcript meta shows `{heard, total}`.
- Risk: interrupt mechanism unverified upstream; the fallback is strictly correct, just
  slower, so the phase cannot dead-end.

**Phase 3 — speculative endpointing** (section 4, exactly)
- Segment-cut recording, eager STT, draft turns with deferred persistence,
  cancel/supersede convention, commit + audio-hold release, completeness gate,
  2-fires cap, eager metrics (drafts fired / committed / superseded per session in the
  footer tooltip).
- `eager_speak` level 2 behind its default-off toggle.
- Verify: (a) speak with a deliberate mid-sentence pause -> no reply fires, final reply
  answers the whole utterance; (b) clean short question -> commit path, endpoint-to-audio
  p50 <= 0.8 s; (c) chat thread history shows no phantom turns after supersedes;
  (d) STT accuracy across segment seams (join of 2-3 segments reads correctly).
- Risks: session pollution quality (watch replies for references to unspoken drafts;
  tune the correction marker), waste ratio (watch the metrics; drop `eagerFires` cap to 1
  if noisy).

**Phase 4 — mixed input/output modes** (section 5)
- Composer + ghost transcript + merge-on-send, typing hold (endpoint deferral + draft
  suppression), `enter_interrupts` + queued-send Badge, Mic / Speak replies quick
  toggles, muted-path streaming captions (all-heard immediately).
- Verify: type while it speaks -> playback unaffected; Enter mid-speech with the setting
  on -> remainder queued then turn sends; with it off -> "queued" Badge then send on
  speech end; Mic off + Speak on covers the operator's listen-and-type flow end-to-end;
  check across widths (1440/1024/768/390) per the UI-check rule.
- Risk: keyboard-focus edge cases with the settings sheet open (Esc precedence covered in
  section 7).

**Phase 5 — chat-side mount** (the agent-chat-redesign seam, small)
- The chat toolbar mic toggle mounts the same `VoiceSession` against the current thread
  (data-mode keys only, per `plan.md:121-124`), with quick toggles only (no settings
  sheet; deep-link to the voice surface for that).
- Verify: voice works from a budget chat dock; builder threads show the toggle disabled
  with a plain hint.

**Phase 6 (optional, only if the heuristic gate proves insufficient) — learned endpointing**
- Local smart-turn v3 (BSD-2, 12 ms CPU inference) as a backend
  `POST /api/voice/turn-complete` scoring the eager segment's audio; the completeness gate
  consults it instead of (not in addition to) the text heuristic at eager time. Keeps the
  all-local constraint. Do not build until Phase 3 metrics show false commits or
  false holds that the heuristic cannot fix.

---

## 9. Research notes: patterns adopted and sources

- **Endpointing beyond fixed silence.** Energy-only VAD with a fixed silence timer is the
  documented root cause of both cut-offs and sluggishness; the field's answer is a
  semantic layer over VAD. LiveKit pairs Silero VAD (presence + interruption) with a
  turn-detector model that modulates the silence timeout between a min and max delay
  (defaults 0.5 s / 3.0 s), and reports ~9.9% false-cutoff at a 300 ms budget for its v1
  model ([LiveKit blog](https://livekit.com/blog/solving-end-of-turn-detection),
  [turn-taking tuning docs](https://docs.livekit.io/agents/logic/turns/tuning/),
  [turn detection overview](https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection)).
  OpenAI Realtime's `semantic_vad` does the same with an `eagerness` knob (low/med/high
  max-timeouts 8/4/2 s) and scores completion probability from the words
  ([OpenAI VAD guide](https://developers.openai.com/api/docs/guides/realtime-vad)).
  Pipecat's smart-turn v3 shows the model can be tiny and local: BSD-2 weights, 12 ms CPU
  inference ([smart-turn v3](https://huggingface.co/pipecat-ai/smart-turn-v3),
  [Daily announcement](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)).
  **Adopted**: configurable base delay + a text completeness gate with a bounded one-shot
  extension (min/max-delay semantics), ML slot deferred to Phase 6.
- **Speculative endpointing with rollback.** Deepgram Flux's eager end-of-turn is the
  cleanest production pattern: `EagerEndOfTurn` (draft) / `TurnResumed` (discard) /
  `EndOfTurn` (finalize), tunable via `eager_eot_threshold` vs `eot_threshold`, at a cost
  of 50-70% more LLM calls
  ([Deepgram eager EOT](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot)).
  LiveKit ships preemptive generation on by default with preemptive TTS off
  (`preemptive_generation.enabled=true`, `preemptive_tts=false`, `max_retries=3`,
  skip when utterance > 10 s)
  ([tuning docs](https://docs.livekit.io/agents/logic/turns/tuning/)); Pipecat tracks the
  same feature ([pipecat #3321](https://github.com/pipecat-ai/pipecat/issues/3321)).
  **Adopted**: two-threshold eager/confirm, draft-and-cancel, generation-eager +
  TTS-on-confirm defaults, fires cap.
- **Barge-in / interruption.** Full duplex (mic hot during TTS) is the dividing line;
  echo handling uses the known TTS output as a reference subtracted from mic energy, plus
  elevated thresholds and sustained-speech requirements; and the classic failure is
  cancelling the LLM/TTS but not flushing the audio queue
  ([Coval on AEC](https://www.coval.ai/blog/voice-ai-echo-cancellation/),
  [softcery architecture comparison](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture)).
  Our MicStream already implements the reference-subtraction pattern
  (`outputProbe`/`echoK`/`bargeSustainMs`, `voice.ts:107-127,158-168`); LiveKit's
  `interruption.min_duration=0.5s` and false-interruption resume informed the level-2
  false-start rule. On preserving context: OpenAI Realtime's
  `conversation.item.truncate` exists specifically so the model's history matches what
  the user actually heard after an interruption
  ([Realtime VAD/interruption docs](https://developers.openai.com/api/docs/guides/realtime-vad)).
  **Adopted**: heard-count reporting + interruption note (truncate analogue), keep our
  echo-aware VAD, flush-audio-first ordering on barge.
- **Streaming TTS pipelining.** Waiting for the full LLM reply adds 1-3 s; the standard
  fix is flushing complete sentences to TTS as the LLM emits them, never splitting
  mid-sentence (choppy prosody), with first-word-in-~300 ms achievable
  ([Deepgram TTS latency](https://developers.deepgram.com/docs/text-to-speech-latency),
  [AssemblyAI streaming pipeline](https://www.assemblyai.com/blog/stream-llm-responses-voice-pipeline-tool-calling-structured-outputs-real-time-actions),
  [latency budget overview](https://www.channel.tel/blog/voice-ai-pipeline-stt-tts-latency-budget)).
  **Adopted**: sentence-boundary cutting on the token stream + render-ahead of 2; our
  existing >= 24-char fragment merge already matches the anti-choppiness guidance.
- **Mixed modality.** OpenAI folded voice into the chat surface (Nov 2025): voice active
  inside the ongoing thread, type-instead-of-speak in the same conversation, live text
  rendering of spoken replies
  ([TechCrunch](https://techcrunch.com/2025/11/25/chatgpts-voice-mode-is-no-longer-a-separate-interface/),
  [OpenAI voice FAQ](https://help.openai.com/en/articles/8400625-voice-mode-faq)).
  **Adopted**: same end state via the shared-engine path (section 5); captions always on;
  composer always available; the two voice lanes are independent toggles.

---

## 10. Constraints check

- Single-operator: no allowlists, no confirmation gates, one worker per session with no
  pool management. The only "guardrails" are engineering bounds (timeouts, fire caps).
- Local STT/TTS stay the default tiers; the only cloud call is the LLM through
  subscription `claude -p` (`llm_router._claude_env()`); `ANTHROPIC_API_KEY` is never
  introduced.
- No committed stubs: every phase lands whole; Phase 6 is explicitly not-built rather
  than stubbed.
- UI copy: no em-dashes, no emoji; lucide icons; primitives from `src/ui`; five status
  kinds only; `check_design.sh` + `tsc --noEmit` gate every UI phase.
