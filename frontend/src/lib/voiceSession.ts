// Voice session — the turn-state machine that makes a spoken exchange behave
// like a real conversation instead of a text dump.
//
// States:   idle → listening → thinking → speaking → (barge-in) → listening
//
// The ideas it implements (see docs/voice-mode/00-overview.md):
//   * heard-vs-generated — the model produces a whole reply, but we voice it one
//     sentence at a time and track exactly which sentences were *finished*
//     before this instant. That is what the user actually heard.
//   * barge-in — while speaking, the mic stays hot; if the user starts talking we
//     stop at the current sentence and report exactly what was heard to the worker
//     (via the turn cancel), so the next reply knows where it was cut. The full
//     reply is still persisted and visible in the transcript.
//   * turn epoch — a monotonically increasing counter, bumped whenever the active
//     turn is abandoned/replaced, that makes every stale async continuation a
//     no-op. This is what keeps the speculative-endpoint pipeline race-free.
//   * adaptation — a rolling interrupt rate is reported back so the persona can
//     be nudged shorter.
//
// Framework-agnostic: React binds via useVoiceSession.

import {
  MicStream,
  ServerSpeaker,
  BrowserSpeaker,
  NativeSpeaker,
  recognizeOnce,
  transcribeBlob,
  nativeTranscribe,
  speakNotice,
  detectCaps,
  splitSentences,
  cleanForSpeech,
  type EngineCaps,
  type Speaker,
  type SttProvider,
  type TtsProvider,
} from "./voice";

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking";

export type SentenceStatus = "pending" | "speaking" | "heard";
export type SpokenSentence = { text: string; status: SentenceStatus };

export type VoicePrefs = {
  voice: string;
  speed: number;
  tts_provider: "auto" | "server" | "browser" | "off";
  stt_provider: "auto" | "server" | "browser";
  // Input lane. When false, the mic is off and the composer is the only input.
  mic_enabled: boolean;
  // Voice-output lane. When false, replies render as captions only (marked
  // heard immediately) — this is the mute, decoupled from the engine choice.
  speak_replies: boolean;
  // Typing holds my turn: while typing, the voice endpoint is deferred.
  typing_hold: boolean;
  typing_hold_ms: number;
  // Enter sends and interrupts ongoing speech (vs. queueing the send).
  enter_interrupts: boolean;
  // Trailing silence (ms) before we take a turn — "Pause before reply".
  endpoint_silence_ms: number;
  // Speculative endpointing: think during an early pause; speak on confirmation.
  eager_endpoint: boolean;
  eager_silence_ms: number;
  barge_in: boolean;
  barge_sensitivity: number;
  verbosity: "terse" | "normal";
};

export const DEFAULT_PREFS: VoicePrefs = {
  voice: "af_heart",
  speed: 1.0,
  tts_provider: "auto",
  stt_provider: "auto",
  mic_enabled: true,
  speak_replies: true,
  typing_hold: true,
  typing_hold_ms: 3000,
  enter_interrupts: true,
  endpoint_silence_ms: 900,
  eager_endpoint: true,
  eager_silence_ms: 400,
  barge_in: true,
  barge_sensitivity: 0.5,
  verbosity: "normal",
};

export type VoiceSnapshot = {
  state: VoiceState;
  // NB: mic level is deliberately NOT here. It changes ~60fps and would force a
  // full React re-render every frame (D4). It travels out of React via the
  // onLevel callback (setOnLevel), which writes a CSS var straight onto the orb.
  partial: string;
  lastUser: string;
  sentences: SpokenSentence[];
  spokenCount: number;
  caps: EngineCaps | null;
  sttProvider: SttProvider | null;
  ttsProvider: TtsProvider | null;
  error: string | null;
  // A fatal, unrecoverable condition (mic permission denied, mic start failure):
  // the UI drops voiceOn and shows the error. Distinct from a per-turn `error`.
  fatal: boolean;
  interruptRate: number;
  // In-progress voice transcript (segments captured but not yet sent) — shown as
  // ghost text above the composer so nothing is sent unseen.
  pendingUser: string;
};

export type ReplyHandle = {
  // Cancel the in-flight generation (barge-in, or superseding a speculative
  // draft). On barge, pass what the user actually heard so the model can be told
  // on the next turn (the truncate analogue); reason distinguishes the two.
  cancel: (info?: { heard?: number; heardText?: string; reason?: string }) => void;
  // Promote a speculative draft to a real turn (flush its deferred persistence).
  commit: () => void;
};

export type ReplyHandlers = {
  // Called with the FULL accumulated reply text so far on each token delta.
  onDelta: (fullText: string) => void;
  onDone: (fullText: string) => void;
  onError: (message: string) => void;
};

export type VoiceConfig = {
  // Start a streaming reply turn. Deltas stream in via handlers; the returned
  // handle cancels/commits the turn. `speculative` starts a draft with deferred
  // persistence; `blocking` forces the non-streaming fallback path (used by the
  // watchdog retry). The binding (useVoiceSession) owns transport + fallback.
  startReply: (text: string, handlers: ReplyHandlers, opts?: { speculative?: boolean; blocking?: boolean }) => ReplyHandle;
  prefs: VoicePrefs;
  onPrefsSignal?: (interruptRate: number) => void;
};

// Words that, when they end a partial utterance, signal it is not finished — so
// the completeness gate holds off firing a speculative draft on that fragment.
const CONTINUATIONS = new Set([
  "and", "but", "or", "so", "because", "although", "though", "which", "that",
  "if", "when", "while", "the", "a", "an", "to", "of", "for", "with", "as",
  "um", "uh", "er", "like", "then", "my", "your", "is", "are", "was", "were",
  "i", "we", "at", "in", "on", "by", "from", "into", "about",
]);

export class VoiceSession {
  private cfg: VoiceConfig;
  private mic = new MicStream();
  private speaker: Speaker | null = null;
  private browserAbort: (() => void) | null = null;

  private state: VoiceState = "idle";
  // Mic level sink: driven ~60fps, but routed to a callback (not a snapshot emit)
  // so it never triggers a React render. The UI writes it onto the orb element.
  private onLevelCb: ((v: number) => void) | null = null;
  private partial = "";
  private lastUser = "";
  private sentences: SpokenSentence[] = [];
  private spokenCount = 0;
  private caps: EngineCaps | null = null;
  private sttProvider: SttProvider | null = null;
  private ttsProvider: TtsProvider | null = null;
  private error: string | null = null;
  private fatal = false;

  // Turn epoch: monotonically increasing, bumped whenever the active turn context
  // is abandoned or replaced (endpoint, supersede, barge, submitText, stop). Every
  // async continuation captures the epoch when it is scheduled and no-ops when it
  // no longer matches — this one mechanism closes the D2 speculative-endpoint
  // races without restructuring the machine. NB: commitDraft does NOT bump — it
  // promotes the SAME draft, whose speaker/handlers must stay live through it.
  private epoch = 0;

  private turnCount = 0;
  private interrupts = 0;
  private capturing = false;
  // Streaming reply state.
  private replyHandle: ReplyHandle | null = null;
  private emitted = 0;              // sentences cut from the stream so far
  private tEndpoint = 0;            // mark: when we committed the turn
  private lastTurnMs: number | null = null;  // endpoint -> first audio (footer)
  // Speculative endpointing state.
  private segs: string[] = [];        // transcribed segments of the current utterance
  private eagerFires = 0;             // eager pauses acted on this utterance (cap 2)
  private resumedSinceCut = false;    // user resumed speaking after the last cut
  private held = false;               // a speculative draft is streaming, held silent
  private draftText = "";             // text the current draft was fired with
  // Mixed input/output state.
  private micOff = false;             // mic lane disabled (composer-only input)
  private typing = false;             // composer currently has text
  private lastKeystroke = 0;
  private typingTimer: ReturnType<typeof setTimeout> | null = null;
  // Browser STT re-arm cap: consecutive empty/failed re-arms before we stop.
  private browserReArms = 0;
  // Client watchdog: a stalled `thinking` (SSE that never errors) is caught here.
  private thinkingTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogRetried = false;    // one automatic retry per user turn
  private listeners = new Set<() => void>();
  // Cached immutable snapshot. Rebuilt only when state mutates (emit invalidates
  // it). useSyncExternalStore compares with Object.is, so returning a stable
  // reference between mutations is what keeps it from looping forever.
  private _snapshot: VoiceSnapshot | null = null;

  constructor(cfg: VoiceConfig) {
    this.cfg = cfg;
  }

  // ---- subscription (React) -----------------------------------------------
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  // Register a sink for the mic level (0..1). Called ~60fps while listening; it
  // deliberately does NOT go through emit()/the snapshot, so the level halo can
  // animate without re-rendering React. Pass null to unregister.
  setOnLevel(cb: ((v: number) => void) | null) { this.onLevelCb = cb; }
  private emit() {
    // Invalidate the cached snapshot so the next read rebuilds from live state,
    // then notify. Between notifications snapshot() returns the same reference.
    this._snapshot = null;
    for (const fn of this.listeners) fn();
  }
  snapshot(): VoiceSnapshot {
    if (this._snapshot) return this._snapshot;
    this._snapshot = {
      state: this.state,
      partial: this.partial,
      lastUser: this.lastUser,
      sentences: this.sentences,
      spokenCount: this.spokenCount,
      caps: this.caps,
      sttProvider: this.sttProvider,
      ttsProvider: this.ttsProvider,
      error: this.error,
      fatal: this.fatal,
      interruptRate: this.turnCount ? this.interrupts / this.turnCount : 0,
      pendingUser: this.segs.join(" ").trim(),
    };
    return this._snapshot;
  }
  private set(s: VoiceState) {
    // The thinking watchdog is armed while (and only while) we are in `thinking`;
    // any state exit clears it.
    if (s !== "thinking") this.clearThinkingWatchdog();
    this.state = s;
    if (s === "thinking") this.armThinkingWatchdog();
    this.emit();
  }

  // Bump the turn epoch. Returns the new value. Called at every point that
  // abandons or replaces the active turn context (see the `epoch` field note).
  private bumpEpoch(): number { return ++this.epoch; }

  setPrefs(p: VoicePrefs) {
    const prev = this.cfg.prefs;
    this.cfg.prefs = p;
    this.mic.setSensitivity(p.barge_sensitivity);
    this.mic.setEndSilence(p.endpoint_silence_ms);
    // Re-resolve providers live so the reply-mute button and the engine choices
    // take effect mid-session instead of only at the next start(). resolveProviders
    // reads speak_replies + tts_provider/stt_provider; it needs caps, so this is a
    // no-op before the first start() (start() resolves once caps land). The newly
    // resolved ttsProvider is what the NEXT turn's beginReply() reads — so unmuting
    // and engine changes apply from the next turn, exactly as intended.
    if (this.caps) this.resolveProviders();
    // Eager endpointing only makes sense on the server STT path (browser/native
    // self-endpoint, so there is no mid-utterance pause for us to act on).
    this.mic.setEager(p.eager_endpoint && this.sttProvider === "server", p.eager_silence_ms);
    // speak_replies toggled OFF while a reply is being spoken: stop the speaker now
    // and let the rest of the reply land as captions. resolveProviders() above has
    // already nulled ttsProvider, so any still-streaming sentences take the muted
    // caption path from here on.
    if (prev.speak_replies && !p.speak_replies && this.state === "speaking") {
      this.muteCurrentSpeech();
    }
    // Live mic-lane toggle once a session is running.
    if (this.state !== "idle" && this.state !== "connecting") this.setMicEnabled(p.mic_enabled);
  }

  // Mute-replies pressed mid-speech. Tear the speaker down (detach first so its
  // stop() cannot re-enter finishSpeaking) and mark every not-yet-heard sentence
  // heard so the captions stay in the log. If the reply is still streaming, the
  // muted-caption path in pushSentences/onReplyDone (ttsProvider is now null) will
  // drive finishSpeaking on done; if the reply already finished and only audio was
  // left, close the turn here. NOT an interrupt — the user still "heard" the rest
  // as text, so we re-arm listening normally (no barge report).
  private muteCurrentSpeech() {
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();
    sp?.stop();
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    for (let i = 0; i < this.sentences.length; i++) {
      if (this.sentences[i].status !== "heard") {
        this.sentences[i] = { ...this.sentences[i], status: "heard" };
      }
    }
    this.spokenCount = this.sentences.length;
    if (this.replyHandle) {
      this.emit();   // reply still in flight — onReplyDone will finishSpeaking
    } else {
      this.finishSpeaking(this.sentences.length, false);
    }
  }

  // Populate engine caps while idle so the settings sheet shows engine truth before
  // voice ever starts. The session is the single caps source: start() re-probes and
  // owns it thereafter; this only fills the pre-first-start gap and no-ops once caps
  // are known or a session has begun.
  async probeCaps() {
    if (this.state !== "idle" || this.caps) return;
    try {
      const c = await detectCaps();
      if (this.state !== "idle" || this.caps) return;   // a session took over meanwhile
      this.caps = c;
      this.emit();
    } catch { /* leave caps null; settings falls back to "Auto" labels */ }
  }

  // ---- lifecycle -----------------------------------------------------------
  async start() {
    if (this.state !== "idle") return;
    this.set("connecting");
    this.error = null;
    this.fatal = false;
    this.caps = await detectCaps();
    this.resolveProviders();
    this.micOff = !this.cfg.prefs.mic_enabled;
    if (!this.micOff) {
      const ok = await this.startMic();
      // A mic failure on the server STT path is fatal — there is no capture lane.
      // startMic() already set `error`; flag it fatal, stay idle, and emit so the
      // UI can drop voiceOn instead of showing a live-looking-but-deaf orb.
      if (!ok && this.sttProvider === "server") { this.fatal = true; this.set("idle"); return; }
    }
    this.armListen();
  }

  private async startMic(): Promise<boolean> {
    try {
      await this.mic.start({
        onLevel: (v) => { this.onLevelCb?.(v); },
        onSpeechStart: () => this.onSpeechStart(),
        onSpeechEnd: () => this.onSpeechEnd(),
        onEagerPause: () => this.onEagerPause(),
        // A recorder-construction failure surfaces here instead of throwing
        // uncaught out of startRecording (Phase 6.1).
        onError: (m) => { this.error = m; this.emit(); },
      });
      this.mic.setSensitivity(this.cfg.prefs.barge_sensitivity);
      this.mic.setEager(this.cfg.prefs.eager_endpoint && this.sttProvider === "server", this.cfg.prefs.eager_silence_ms);
      return true;
    } catch (e) {
      // The VAD mic drives capture + hands-free barge-in for the SERVER path.
      // Browser and native STT capture themselves, so a mic failure there is
      // non-fatal — proceed without VAD.
      if (this.sttProvider === "server") { this.error = `mic: ${String(e)}`; return false; }
      return true;
    }
  }

  // Toggle the mic lane live without ending the session (composer stays usable).
  setMicEnabled(on: boolean) {
    if (on === !this.micOff) return;
    this.micOff = !on;
    if (!on) {
      this.mic.stop();
      this.capturing = false;
      this.emit();
    } else if (this.state !== "idle" && this.state !== "connecting") {
      this.startMic().then((ok) => { if (ok) this.armListen(); });
    }
  }

  stop() {
    this.bumpEpoch();
    this._interrupted = true;
    this.replyHandle?.cancel();
    this.replyHandle = null;
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();          // its stop() must not fire finishSpeaking
    sp?.stop();
    this.browserAbort?.();
    this.mic.stop();
    this.capturing = false;
    this.clearThinkingWatchdog();
    this.cfg.onPrefsSignal?.(this.turnCount ? this.interrupts / this.turnCount : 0);
    this.set("idle");
  }

  private resolveProviders() {
    const p = this.cfg.prefs;
    const c = this.caps!;
    // STT
    if (p.stt_provider === "server" && c.server?.stt.available) this.sttProvider = "server";
    else if (p.stt_provider === "browser" && c.browserStt) this.sttProvider = "browser";
    else this.sttProvider = c.stt; // auto
    // TTS
    if (!p.speak_replies || p.tts_provider === "off") this.ttsProvider = null;
    else if (p.tts_provider === "server" && c.server?.tts.available) this.ttsProvider = "server";
    else if (p.tts_provider === "browser" && c.browserTts) this.ttsProvider = "browser";
    else this.ttsProvider = c.tts; // auto
  }

  // ---- listening -----------------------------------------------------------
  private armListen() {
    this.partial = "";
    this.browserReArms = 0;
    this.set("listening");
    if (this.micOff) return;   // composer-only: no mic capture
    if (this.sttProvider === "browser") this.armBrowserListen();
    else if (this.sttProvider === "native") this.armNativeListen();
    else this.armServerListen();
  }

  private armNativeListen() {
    nativeTranscribe()
      .then((text) => this.onUtterance(text))
      .catch((e) => {
        this.error = `stt: ${String(e)}`;
        this.emit();
        // fall back if native STT is unusable
        this.sttProvider = this.caps?.server?.stt.available ? "server"
          : this.caps?.browserStt ? "browser" : this.sttProvider;
        if (this.sttProvider !== "native") this.armListen();
      });
  }

  private armServerListen() {
    // Record continuously; VAD's speechEnd cuts the utterance for transcription.
    if (!this.capturing) {
      this.mic.startRecording();
      this.capturing = true;
    }
  }

  private armBrowserListen() {
    try {
      const { promise, abort } = recognizeOnce((t) => { this.partial = t; this.emit(); });
      this.browserAbort = abort;
      promise
        .then((text) => {
          this.browserAbort = null;
          const t = (text || "").trim();
          if (t) { this.browserReArms = 0; this.onUtterance(t); return; }
          this.reArmBrowser();   // empty result: bounded re-arm
        })
        .catch((e) => {
          this.browserAbort = null;
          const msg = String((e as Error)?.message || e);
          // Permission denied is permanent — a re-arm just fails identically and
          // spins forever. Flag it fatal so the UI drops voiceOn.
          if (/not-allowed|denied|service-not-allowed/i.test(msg)) {
            this.error = "Microphone permission denied.";
            this.fatal = true;
            this.emit();
            return;
          }
          this.reArmBrowser(msg);
        });
    } catch {
      // no browser recognizer → try server recorder path
      this.sttProvider = this.caps?.server?.stt.available ? "server" : this.sttProvider;
      this.armServerListen();
    }
  }

  // Re-arm the browser recognizer after an empty/failed result, capped at 3
  // consecutive misfires so a wedged recognizer can't spin an infinite loop.
  private reArmBrowser(failMsg?: string) {
    if (this.state !== "listening") return;
    if (++this.browserReArms >= 3) {
      this.error = failMsg ? `stt: ${failMsg}` : "No speech detected. Tap the mic to retry.";
      this.browserReArms = 0;
      this.emit();
      return;
    }
    this.armBrowserListen();
  }

  private onSpeechStart() {
    // Barge-in: talking over the assistant stops it mid-sentence.
    if (this.state === "speaking" && this.cfg.prefs.barge_in) {
      this.bargeIn();
      return;
    }
    // Resume while listening: the user kept talking, so any speculative draft was
    // answering a fragment. Drop it (superseded) and keep collecting the utterance.
    if (this.state === "listening") {
      this.resumedSinceCut = true;
      if (this.held) this.supersedeDraft();
    }
  }

  private async onSpeechEnd() {
    // Only the server (recorder) path acts on VAD end; browser + native STT
    // self-endpoint.
    if (this.state !== "listening" || this.sttProvider !== "server") return;
    if (!this.capturing) return;
    const ep = this.epoch;
    this.capturing = false;
    // Cut the final segment and add it to any eager segments already transcribed.
    let blob: Blob | null = null;
    try { blob = await this.mic.stopRecording(); } catch { blob = null; }
    // Superseded/interrupted while the recorder was flushing (Enter-send, barge):
    // abandon — whoever bumped the epoch owns the turn now.
    if (ep !== this.epoch || this.state !== "listening") return;
    if (blob && blob.size >= 1200) {
      try { const t = await transcribeBlob(blob); if (t) this.segs.push(t); }
      catch { /* keep whatever segments we have */ }
    }
    if (ep !== this.epoch || this.state !== "listening") return;
    // The user is typing — hold this turn open (defer the endpoint) and keep
    // capturing; onTypingReleased or an Enter-send will finalize it. Bump so any
    // eager continuation still in flight for this pause no-ops.
    if (this.typingHoldActive()) {
      this.bumpEpoch();
      if (this.held) this.supersedeDraft();
      this.armServerListen();
      this.emit();
      return;
    }
    const final = this.segs.join(" ").trim();
    if (!final) {
      // Empty endpoint. Bump so a late eager transcribe can't push text into the
      // reset segs and fire a phantom held draft with the user already silent.
      this.bumpEpoch();
      if (this.held) this.supersedeDraft();
      this.resetSegs();
      this.armServerListen();
      return;
    }
    if (this.held && this.draftText === final) {
      // Confirmed: the draft answered the whole utterance. Release its audio.
      // commitDraft deliberately does NOT bump — the draft's speaker keeps playing.
      this.commitDraft(final);
      this.resetSegs();
      return;
    }
    // No draft, or the utterance grew past what the draft answered: start a fresh
    // real turn. The endpoint bump invalidates any in-flight eager continuation.
    if (this.held) this.supersedeDraft();
    this.resetSegs();
    this.bumpEpoch();
    this.lastUser = final;
    this.turnCount += 1;
    this.tEndpoint = performance.now();
    this.set("thinking");
    this.beginReply(final);
  }

  // Eager pause: a short silence mid-utterance. Cut a segment, transcribe it, and
  // (if the utterance looks complete and the user has not resumed) fire a held
  // speculative draft so a confirmed endpoint can speak with near-zero latency.
  private async onEagerPause() {
    if (this.state !== "listening" || this.sttProvider !== "server") return;
    if (!this.cfg.prefs.eager_endpoint || !this.ttsProvider) return;
    if (this.held || !this.capturing) return;
    if (this.typingHoldActive()) return;   // the user is typing — wait for them
    if (this.eagerFires >= 2) return;
    // Capture the epoch at schedule time. If the real endpoint (or a barge, or an
    // Enter-send) fires during either await below, the epoch moves and every
    // continuation here becomes a no-op — this closes D2 races (a) and (c): stale
    // eager text never lands in a fresh utterance and never fires a phantom draft.
    const ep = this.epoch;
    this.eagerFires += 1;
    this.resumedSinceCut = false;
    let blob: Blob | null = null;
    try { blob = await this.mic.cutSegment(); } catch { blob = null; }
    if (this.state !== "listening" || !this.capturing || ep !== this.epoch) return;
    if (!blob || blob.size < 1200) return;
    let text = "";
    try { text = await transcribeBlob(blob); } catch { return; }
    if (this.state !== "listening" || !this.capturing || ep !== this.epoch) return;
    if (text) this.segs.push(text);
    if (this.resumedSinceCut) return;         // user resumed during STT — no draft
    const joined = this.segs.join(" ").trim();
    if (!joined || this.incomplete(joined)) return;  // completeness gate
    this.draftText = joined;
    this.lastUser = joined;
    this.beginReply(joined, true);            // held speculative draft
  }

  // A pure-text completeness heuristic (no model): suppress speculation when the
  // utterance clearly is not finished (trailing conjunction/filler/open bracket).
  private incomplete(text: string): boolean {
    const t = text.trim().toLowerCase();
    if (/[,;:([{"'—-]$/.test(t)) return true;
    const last = t.split(/\s+/).pop() || "";
    return CONTINUATIONS.has(last);
  }

  private resetSegs() {
    this.segs = [];
    this.eagerFires = 0;
    this.resumedSinceCut = false;
  }

  private commitDraft(_final: string) {
    this.held = false;
    this.replyHandle?.commit();
    this.replyHandle = null;
    if (this.ttsProvider && this.speaker) {
      const sp = this.speaker;
      this.mic.setOutputProbe(() => (sp.outputLevel ? sp.outputLevel() : 0));
      this.mic.setBargeMode(true);
      this.armBargePreRoll();   // Phase 6.4
      this.tEndpoint = performance.now();
      this.lastTurnMs = null;
      this.turnCount += 1;
      this.set("speaking");
      if (sp.setHeld) sp.setHeld(false);   // start playing the pre-rendered audio
    } else {
      this.finishSpeaking(this.sentences.length, false);
    }
  }

  private supersedeDraft() {
    // Abandon the held draft. The epoch bump makes the draft's reply handlers and
    // speaker events no-op; detach() nulls the speaker's sink so its stop() cannot
    // fire finishSpeaking. Together these kill D2 bullet 2: no phantom queue entry
    // and no mid-utterance mic.startRecording() — supersede never touches the
    // recorder at all, so the utterance the user is still speaking is not clipped.
    this.bumpEpoch();
    this.replyHandle?.cancel({ reason: "superseded" });
    this.replyHandle = null;
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();
    sp?.stop();
    this.held = false;
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    this.sentences = [];
    this.spokenCount = 0;
    this.emitted = 0;
    this.emit();
  }

  // ---- a completed user utterance -----------------------------------------
  private onUtterance(raw: string) {
    const text = (raw || "").trim();
    this.partial = "";
    if (!text) { this.armListen(); return; }
    this.startTurn(text);
  }

  // Kick off a (non-speculative) reply turn for a finished utterance.
  private startTurn(text: string) {
    this.bumpEpoch();
    this.lastUser = text;
    this.turnCount += 1;
    this.tEndpoint = performance.now();
    this.set("thinking");
    this.beginReply(text);
  }

  // ---- typed input (mixed modes) ------------------------------------------
  // Send a typed message. Merges any pending voice segments captured this turn,
  // so "type over" what you were saying sends both, transcript first.
  submitText(raw: string) {
    const typed = (raw || "").trim();
    const merged = [this.segs.join(" ").trim(), typed].filter(Boolean).join(" ").trim();
    this.resetSegs();
    this.typing = false;
    if (!merged) return;
    // Enter always interrupts and always sends — no queued-send path. The epoch
    // bump abandons any in-flight eager continuation immediately.
    this.bumpEpoch();
    if (this.state === "speaking") this.bargeIn();   // stop speech, report heard
    else if (this.held) this.supersedeDraft();
    this.startTurn(merged);
  }

  // Typing is a soft turn-hold: while the composer has text (or a keystroke
  // landed recently) the voice endpoint is deferred, as if the user is still
  // "speaking". Called by the composer on every change.
  setTyping(active: boolean) {
    this.typing = active;
    this.lastKeystroke = performance.now();
    if (this.typingTimer) clearTimeout(this.typingTimer);
    if (!active) {
      this.typingTimer = setTimeout(() => this.onTypingReleased(), this.cfg.prefs.typing_hold_ms);
    }
  }

  private typingHoldActive(): boolean {
    if (!this.cfg.prefs.typing_hold) return false;
    return this.typing || (performance.now() - this.lastKeystroke < this.cfg.prefs.typing_hold_ms);
  }

  // When the typing hold lapses with the composer empty, finalize any voice
  // segments that were deferred (the endpoint we held off on).
  private onTypingReleased() {
    if (this.typingHoldActive()) return;
    if (!this.typing && this.segs.length && this.state === "listening") {
      const t = this.segs.join(" ").trim();
      this.resetSegs();
      if (t) this.startTurn(t);
    }
  }

  // ---- streaming reply -----------------------------------------------------
  // speculative: start a HELD draft during listening (generation streams and TTS
  // renders ahead, but no audio plays and the state stays "listening" until the
  // endpoint confirms and commitDraft() releases it). blocking: force the
  // non-streaming fallback (the watchdog retry).
  private beginReply(text: string, speculative = false, blocking = false) {
    // A fresh, user-initiated real turn resets the one-retry watchdog budget.
    if (!speculative && !blocking) this.watchdogRetried = false;
    // Every continuation below is bound to this epoch; if the turn is abandoned
    // (supersede/barge/new endpoint/stop), the epoch moves and they no-op.
    const ep = this.epoch;
    this.emitted = 0;
    this.sentences = [];
    this.spokenCount = 0;
    this._interrupted = false;
    this.held = speculative;
    if (!speculative) this.error = null;
    this.lastTurnMs = null;
    // Set up the speaker (streaming) unless muted. Muted replies still stream in
    // as captions, marked heard as they land.
    if (this.ttsProvider) {
      this.speaker = this.makeSpeaker();
      const sp = this.speaker;
      const ev = {
        onSentenceStart: (i: number) => {
          if (ep !== this.epoch) return;
          if (this.lastTurnMs === null) this.lastTurnMs = Math.round(performance.now() - this.tEndpoint);
          this.sentences[i] = { ...this.sentences[i], status: "speaking" };
          this.spokenCount = i;
          this.emit();
        },
        onSentenceEnd: (i: number) => {
          if (ep !== this.epoch) return;
          this.sentences[i] = { ...this.sentences[i], status: "heard" };
          this.spokenCount = i + 1;
          this.emit();
        },
        onDone: (reached: number) => { if (ep !== this.epoch) return; this.finishSpeaking(reached, false); },
        // Autoplay blocked the held-draft release (D6): tell the user how to
        // recover instead of dropping audio silently. Tapping the mic re-runs
        // unlockAudio() from a gesture.
        onBlocked: () => { if (ep !== this.epoch) return; this.error = "Tap the mic to enable audio."; this.emit(); },
      };
      if (sp.begin) sp.begin(ev); else sp.speak([], ev);  // begin streaming
      if (speculative && sp.setHeld) {
        sp.setHeld(true);   // render ahead, do not play until commit
      } else {
        // A real (or unheldable) turn is speaking now: arm echo-aware barge-in.
        this.mic.setOutputProbe(() => (sp.outputLevel ? sp.outputLevel() : 0));
        this.mic.setBargeMode(true);
        this.armBargePreRoll();   // Phase 6.4
      }
    }
    this.replyHandle = this.cfg.startReply(text, {
      onDelta: (full) => { if (ep !== this.epoch) return; this.onReplyDelta(full); },
      onDone: (full) => { if (ep !== this.epoch) return; this.onReplyDone(full); },
      onError: (msg) => { if (ep !== this.epoch) return; this.onReplyError(msg); },
    }, { speculative, blocking });
  }

  // Cut complete sentences from the growing buffer and hand them to the speaker.
  // The trailing fragment is held back (it may still be growing) until `final`.
  private pushSentences(full: string, final: boolean) {
    const sents = splitSentences(cleanForSpeech(full));
    const upto = final ? sents.length : Math.max(0, sents.length - 1);
    for (; this.emitted < upto; this.emitted++) {
      const body = sents[this.emitted].trim();
      if (!body) continue;
      const i = this.sentences.length;
      this.sentences.push({ text: body, status: "pending" });
      if (this.ttsProvider && this.speaker) {
        // A held draft keeps rendering ahead but stays "listening" and silent.
        if (!this.held && this.state !== "speaking") this.set("speaking");
        if (this.speaker.enqueue) this.speaker.enqueue(body);
        else this.speaker.speak([body], {});  // non-streaming speaker fallback
      } else if (!this.held) {
        // Muted: caption only, heard immediately.
        this.sentences[i] = { ...this.sentences[i], status: "heard" };
        this.spokenCount = i + 1;
      }
    }
    this.emit();
  }

  private onReplyDelta(full: string) {
    if (this._interrupted) return;
    this.pushSentences(full, false);
  }

  private onReplyDone(full: string) {
    if (this._interrupted) return;
    // Keep the handle for a held draft so commit/supersede can still reach it.
    if (!this.held) this.replyHandle = null;
    this.pushSentences(full, true);
    if (this.ttsProvider && this.speaker) {
      if (this.speaker.end) this.speaker.end();
      // onDone(reached) from the speaker drives finishSpeaking once playback ends
      // (for a held draft, playback only begins after commitDraft releases it).
    } else if (!this.held) {
      // Muted path: nothing to play, go straight back to listening.
      this.finishSpeaking(this.sentences.length, false);
    }
  }

  private onReplyError(msg: string) {
    if (this._interrupted) return;
    // A failed draft is silently abandoned; the endpoint will start a real turn.
    if (this.held) { this.supersedeDraft(); return; }
    this.replyHandle = null;
    // Detach + bump before stop() so the speaker's stop()->onDone can't re-enter
    // finishSpeaking (which would double-arm listening) — symmetric with the other
    // teardown sites (supersedeDraft, stop, bargeIn, muteCurrentSpeech, watchdog).
    this.bumpEpoch();
    this.speaker?.detach();
    this.speaker?.stop();
    this.speaker = null;
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    this.error = `reply: ${msg}`;
    this.emit();
    this.armListen();
  }

  private makeSpeaker(): Speaker {
    const p = this.cfg.prefs;
    if (this.ttsProvider === "server") return new ServerSpeaker({ voice: p.voice, speed: p.speed });
    if (this.ttsProvider === "native") return new NativeSpeaker({ speed: p.speed });
    return new BrowserSpeaker({ voiceName: undefined, rate: p.speed });
  }

  // ---- barge pre-roll (Phase 6.4) -----------------------------------------
  // SELF-CONTAINED + REVERTABLE. Keep the recorder hot through the assistant's
  // speech (server STT path only, where we own the recorder) so an interruption
  // is captured from its first word instead of after a cold restart. The buffer
  // accumulated during speech holds the assistant's own TTS echo, so it is always
  // discarded (at barge confirm, or at normal completion) before the user's next
  // turn is transcribed. If weak AEC bleeds TTS into transcripts, revert this
  // method, its two call sites in beginReply/commitDraft, the discardSegment call
  // in bargeIn, and the capturing guards in finishSpeaking — nothing else depends
  // on it (with pre-roll off, `capturing` is false at those points and the old
  // cold-start behavior is restored unchanged).
  private armBargePreRoll() {
    if (this.sttProvider !== "server" || this.micOff) return;
    if (!this.capturing) { this.mic.startRecording(); this.capturing = true; }
  }

  private bargeIn() {
    this.interrupts += 1;
    // Stop generation at the worker (reporting exactly what was heard so the next
    // turn knows where it was cut), then tear the speaker down and re-arm. We bump
    // the epoch and detach the speaker so its stop() cannot re-enter finishSpeaking
    // — we call finishSpeaking exactly once, ourselves, with the heard count.
    this._interrupted = true;
    const heard = this.spokenCount;
    const heardText = heard > 0 ? this.sentences[heard - 1]?.text : undefined;
    // Barge pre-roll (Phase 6.4): the recorder has been hot since speaking start.
    // Discard the pre-barge segment (assistant echo + the ~180ms sustain window)
    // and keep a fresh recorder running so the interruption is captured with no
    // restart gap. finishSpeaking then reuses this hot recorder (its capturing
    // guard skips the cold start).
    if (this.sttProvider === "server" && !this.micOff) {
      this.mic.discardSegment();
      this.capturing = true;
    }
    this.replyHandle?.cancel({ heard, heardText });
    this.replyHandle = null;
    this.bumpEpoch();
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();
    sp?.stop();
    this.finishSpeaking(heard, false);
  }
  private _interrupted = false;

  private finishSpeaking(reached: number, _normal: boolean) {
    const interrupted = this._interrupted;
    this._interrupted = false;
    this.replyHandle = null;
    this.held = false;
    this.speaker = null;
    // Leave barge mode; normal-sensitivity VAD for the next user utterance.
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    void reached;   // heard accounting is tracked live via onSentenceEnd/spokenCount
    // Report the rolling interrupt rate so the persona can be nudged shorter. The
    // interruption itself (what was heard) was reported to the worker at cancel().
    this.cfg.onPrefsSignal?.(this.turnCount ? this.interrupts / this.turnCount : 0);
    // After a barge-in on the server path the user is already talking → start
    // capturing that utterance immediately. Browser/native STT re-arm normally.
    if (interrupted && this.sttProvider === "server") {
      // With barge pre-roll (Phase 6.4) bargeIn already left a fresh recorder hot;
      // reuse it. Only cold-start when pre-roll is off (capturing false).
      if (!this.capturing) { this.mic.startRecording(); this.capturing = true; }
      this.set("listening");
    } else {
      // Normal completion. If barge pre-roll kept a recorder hot through the whole
      // reply, its buffer holds the assistant's own audio — discard it so the
      // user's next turn is captured fresh (Phase 6.4). No-op when pre-roll is off
      // (capturing already false → armServerListen cold-starts as before).
      if (this.capturing && this.sttProvider === "server") this.mic.discardSegment();
      this.armListen();
    }
  }

  // ---- client watchdog -----------------------------------------------------
  // A stalled `thinking` (an SSE that hangs without erroring) would otherwise
  // wedge the machine forever. Armed on entry to `thinking`, cleared on any exit.
  private static readonly THINKING_TIMEOUT_MS = 70000;

  private armThinkingWatchdog() {
    this.clearThinkingWatchdog();
    this.thinkingTimer = setTimeout(() => this.onThinkingTimeout(), VoiceSession.THINKING_TIMEOUT_MS);
  }
  private clearThinkingWatchdog() {
    if (this.thinkingTimer) { clearTimeout(this.thinkingTimer); this.thinkingTimer = null; }
  }
  private onThinkingTimeout() {
    if (this.state !== "thinking") return;
    // Tear the stuck turn down (epoch bump + detach make its late continuations
    // no-op) and give a visible + spoken failure signal.
    this.bumpEpoch();
    this.replyHandle?.cancel({ reason: "timeout" });
    this.replyHandle = null;
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();
    sp?.stop();
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    if (this.watchdogRetried) {
      // The retry also stalled — stop retrying, surface the error, go listen.
      this.error = "Reply failed. Tap the mic to try again.";
      this.emit();
      this.armListen();
      return;
    }
    this.watchdogRetried = true;
    this.error = "That one failed, retrying.";
    if (this.cfg.prefs.speak_replies && this.ttsProvider) speakNotice("That one failed, retrying.");
    this.emit();
    this.retryTurnBlocking(this.lastUser);
  }

  // One automatic retry of the current turn via the blocking (non-streaming)
  // fallback path. Keeps watchdogRetried set so a second stall gives up.
  private retryTurnBlocking(text: string) {
    const t = (text || "").trim();
    if (!t) { this.armListen(); return; }
    this.bumpEpoch();
    this.lastUser = t;
    this.tEndpoint = performance.now();
    this.set("thinking");            // re-arms the watchdog for the retry
    this.beginReply(t, false, true); // blocking = force the fallback
  }

  // ---- manual controls -----------------------------------------------------
  // Force-end the current utterance (tap instead of waiting for silence).
  forceEndUtterance() {
    if (this.state === "listening" && this.sttProvider !== "browser") this.onSpeechEnd();
    else this.browserAbort?.();
  }
  interrupt() {
    if (this.state === "speaking") this.bargeIn();
  }
  // Scrap the current turn: abort the in-flight reply AND drop the utterance that
  // triggered it, so a mis-heard auto-send is fully recoverable. The server also
  // discards it (reason "cancelled" -> voice_worker skips persistence), so the
  // mistake never lands in the transcript. Works while thinking OR speaking;
  // returns straight to listening. Mirrors supersedeDraft's teardown.
  cancelTurn() {
    if (this.state !== "thinking" && this.state !== "speaking") return;
    this.bumpEpoch();
    this.replyHandle?.cancel({ reason: "cancelled" });
    this.replyHandle = null;
    const sp = this.speaker;
    this.speaker = null;
    sp?.detach();
    sp?.stop();
    this.held = false;
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    this.sentences = [];
    this.spokenCount = 0;
    this.emitted = 0;
    this.lastUser = "";            // drop the mis-heard utterance from the live view
    this.armListen();
    this.emit();
  }
  // Retry from the status strip after a (non-fatal) error: clear the error and
  // re-issue the last user turn if there is one, else just re-arm listening. The
  // startTurn/armListen bump the epoch, so any stale continuation is abandoned.
  retry() {
    if (this.state === "idle" || this.state === "connecting") return;
    this.error = null;
    const t = (this.lastUser || "").trim();
    if (t) this.startTurn(t);
    else this.armListen();
  }
}
