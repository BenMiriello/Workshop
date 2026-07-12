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
//     stop at the current sentence, and the UNSPOKEN remainder is not lost — it
//     becomes a background thread the assistant can offer later.
//   * background-thread queue — interrupted remainders (and any explicit
//     ⟦aside:…⟧ markers) collect here; they are offered, never auto-played.
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
  speakFiller,
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

export type QueuedThread = { id: string; topic: string; text: string; from: "interrupt" | "aside" };

export type VoicePrefs = {
  voice: string;
  speed: number;
  tts_provider: "auto" | "server" | "browser" | "off";
  stt_provider: "auto" | "server" | "browser";
  // Voice-output lane. When false, replies render as captions only (marked
  // heard immediately) — this is the mute, decoupled from the engine choice.
  speak_replies: boolean;
  // Trailing silence (ms) before we take a turn — "Pause before reply".
  endpoint_silence_ms: number;
  barge_in: boolean;
  barge_sensitivity: number;
  verbosity: "terse" | "normal";
};

export const DEFAULT_PREFS: VoicePrefs = {
  voice: "af_heart",
  speed: 1.0,
  tts_provider: "auto",
  stt_provider: "auto",
  speak_replies: true,
  endpoint_silence_ms: 900,
  barge_in: true,
  barge_sensitivity: 0.5,
  verbosity: "normal",
};

export type VoiceSnapshot = {
  state: VoiceState;
  level: number;
  partial: string;
  lastUser: string;
  sentences: SpokenSentence[];
  spokenCount: number;
  queue: QueuedThread[];
  caps: EngineCaps | null;
  sttProvider: SttProvider | null;
  ttsProvider: TtsProvider | null;
  error: string | null;
  interruptRate: number;
  // Endpoint -> first-audio latency of the last turn (ms), for the footer readout.
  lastTurnMs: number | null;
};

export type ReplyHandle = {
  // Cancel the in-flight generation. On barge-in, pass what the user actually
  // heard so the model can be told on the next turn (the truncate analogue).
  cancel: (info?: { heard?: number; heardText?: string }) => void;
};

export type ReplyHandlers = {
  // Called with the FULL accumulated reply text so far on each token delta.
  onDelta: (fullText: string) => void;
  onDone: (fullText: string) => void;
  onError: (message: string) => void;
};

export type VoiceConfig = {
  // Start a streaming reply turn. Deltas stream in via handlers; the returned
  // handle cancels the in-flight generation (barge-in / supersede). The binding
  // (useVoiceSession) owns transport + any fallback to a blocking call.
  startReply: (text: string, handlers: ReplyHandlers) => ReplyHandle;
  prefs: VoicePrefs;
  onPrefsSignal?: (interruptRate: number) => void;
};

const ASIDE_RE = /[⟦\[]{1,2}\s*aside:\s*([^⟧\]]+)[⟧\]]{1,2}/gi;

let _tid = 0;

export class VoiceSession {
  private cfg: VoiceConfig;
  private mic = new MicStream();
  private speaker: Speaker | null = null;
  private browserAbort: (() => void) | null = null;

  private state: VoiceState = "idle";
  private level = 0;
  private partial = "";
  private lastUser = "";
  private sentences: SpokenSentence[] = [];
  private spokenCount = 0;
  private queue: QueuedThread[] = [];
  private caps: EngineCaps | null = null;
  private sttProvider: SttProvider | null = null;
  private ttsProvider: TtsProvider | null = null;
  private error: string | null = null;

  private turnCount = 0;
  private interrupts = 0;
  private capturing = false;
  // Streaming reply state.
  private replyHandle: ReplyHandle | null = null;
  private emitted = 0;              // sentences cut from the stream so far
  private tEndpoint = 0;            // mark: when we committed the turn
  private lastTurnMs: number | null = null;  // endpoint -> first audio (footer)
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
      level: this.level,
      partial: this.partial,
      lastUser: this.lastUser,
      sentences: this.sentences,
      spokenCount: this.spokenCount,
      queue: this.queue,
      caps: this.caps,
      sttProvider: this.sttProvider,
      ttsProvider: this.ttsProvider,
      error: this.error,
      interruptRate: this.turnCount ? this.interrupts / this.turnCount : 0,
      lastTurnMs: this.lastTurnMs,
    };
    return this._snapshot;
  }
  private set(s: VoiceState) {
    this.state = s;
    this.emit();
  }

  setPrefs(p: VoicePrefs) {
    this.cfg.prefs = p;
    this.mic.setSensitivity(p.barge_sensitivity);
    this.mic.setEndSilence(p.endpoint_silence_ms);
  }

  // ---- lifecycle -----------------------------------------------------------
  async start() {
    if (this.state !== "idle") return;
    this.set("connecting");
    this.error = null;
    this.caps = await detectCaps();
    this.resolveProviders();
    try {
      await this.mic.start({
        onLevel: (v) => { this.level = v; this.emit(); },
        onSpeechStart: () => this.onSpeechStart(),
        onSpeechEnd: () => this.onSpeechEnd(),
      });
      this.mic.setSensitivity(this.cfg.prefs.barge_sensitivity);
    } catch (e) {
      // The VAD mic drives capture + hands-free barge-in for the SERVER path.
      // Browser and native STT capture themselves and interrupt via tap, so a
      // mic failure there is non-fatal — proceed without VAD.
      if (this.sttProvider === "server") {
        this.error = `mic: ${String(e)}`;
        this.set("idle");
        return;
      }
    }
    this.armListen();
  }

  stop() {
    this._interrupted = true;
    this.replyHandle?.cancel();
    this.replyHandle = null;
    this.speaker?.stop();
    this.browserAbort?.();
    this.mic.stop();
    this.capturing = false;
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
    this.set("listening");
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
        .then((text) => { this.browserAbort = null; this.onUtterance(text); })
        .catch(() => { this.browserAbort = null; if (this.state === "listening") this.armBrowserListen(); });
    } catch {
      // no browser recognizer → try server recorder path
      this.sttProvider = this.caps?.server?.stt.available ? "server" : this.sttProvider;
      this.armServerListen();
    }
  }

  private onSpeechStart() {
    // Barge-in: talking over the assistant stops it mid-sentence.
    if (this.state === "speaking" && this.cfg.prefs.barge_in) {
      this.bargeIn();
    }
  }

  private async onSpeechEnd() {
    // Only the server (recorder) path acts on VAD end; browser + native STT
    // self-endpoint.
    if (this.state !== "listening" || this.sttProvider !== "server") return;
    if (!this.capturing) return;
    this.capturing = false;
    const blob = await this.mic.stopRecording();
    if (!blob || blob.size < 1200) { this.armServerListen(); return; }
    this.set("thinking");
    try {
      const text = await transcribeBlob(blob);
      this.onUtterance(text);
    } catch (e) {
      this.error = `stt: ${String(e)}`;
      this.emit();
      this.armListen();
    }
  }

  // ---- a completed user utterance -----------------------------------------
  private onUtterance(raw: string) {
    const text = (raw || "").trim();
    this.partial = "";
    if (!text) { this.armListen(); return; }
    this.lastUser = text;
    this.turnCount += 1;
    this.tEndpoint = performance.now();
    this.set("thinking");
    // Cold-start filler so there's no dead air while a server session warms up.
    if (this.turnCount === 1 && this.ttsProvider === "server") speakFiller("One moment.");
    this.beginReply(text);
  }

  // ---- streaming reply -----------------------------------------------------
  // Extract ⟦aside:…⟧ markers from a single sentence before it is spoken/queued.
  private extractAsides(reply: string): { body: string; asides: QueuedThread[] } {
    const asides: QueuedThread[] = [];
    const body = reply.replace(ASIDE_RE, (_m, topic) => {
      asides.push({ id: `t${++_tid}`, topic: String(topic).trim(), text: String(topic).trim(), from: "aside" });
      return "";
    }).trim();
    return { body, asides };
  }

  private beginReply(text: string) {
    this.emitted = 0;
    this.sentences = [];
    this.spokenCount = 0;
    this._interrupted = false;
    this.error = null;
    // Set up the speaker (streaming) unless muted. Muted replies still stream in
    // as captions, marked heard as they land.
    if (this.ttsProvider) {
      this.speaker = this.makeSpeaker();
      const sp = this.speaker;
      this.mic.setOutputProbe(() => (sp.outputLevel ? sp.outputLevel() : 0));
      this.mic.setBargeMode(true);
      const ev = {
        onSentenceStart: (i: number) => {
          if (this.lastTurnMs === null) this.lastTurnMs = Math.round(performance.now() - this.tEndpoint);
          this.sentences[i] = { ...this.sentences[i], status: "speaking" };
          this.spokenCount = i;
          this.emit();
        },
        onSentenceEnd: (i: number) => {
          this.sentences[i] = { ...this.sentences[i], status: "heard" };
          this.spokenCount = i + 1;
          this.emit();
        },
        onDone: (reached: number) => this.finishSpeaking(reached, false),
      };
      if (sp.begin) sp.begin(ev); else sp.speak([], ev);  // begin streaming
    }
    this.lastTurnMs = null;
    this.replyHandle = this.cfg.startReply(text, {
      onDelta: (full) => this.onReplyDelta(full),
      onDone: (full) => this.onReplyDone(full),
      onError: (msg) => this.onReplyError(msg),
    });
  }

  // Cut complete sentences from the growing buffer and hand them to the speaker.
  // The trailing fragment is held back (it may still be growing) until `final`.
  private pushSentences(full: string, final: boolean) {
    const sents = splitSentences(cleanForSpeech(full));
    const upto = final ? sents.length : Math.max(0, sents.length - 1);
    for (; this.emitted < upto; this.emitted++) {
      const { body, asides } = this.extractAsides(sents[this.emitted]);
      if (asides.length) this.queue = [...this.queue, ...asides];
      if (!body) continue;
      const i = this.sentences.length;
      this.sentences.push({ text: body, status: "pending" });
      if (this.ttsProvider && this.speaker) {
        if (this.state !== "speaking") this.set("speaking");
        if (this.speaker.enqueue) this.speaker.enqueue(body);
        else this.speaker.speak([body], {});  // non-streaming speaker fallback
      } else {
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
    this.replyHandle = null;
    if (this._interrupted) return;
    this.pushSentences(full, true);
    if (this.ttsProvider && this.speaker) {
      if (this.speaker.end) this.speaker.end();
      // onDone(reached) from the speaker drives finishSpeaking once playback ends.
    } else {
      // Muted path: nothing to play, go straight back to listening.
      this.finishSpeaking(this.sentences.length, false);
    }
  }

  private onReplyError(msg: string) {
    this.replyHandle = null;
    if (this._interrupted) return;
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

  private bargeIn() {
    this.interrupts += 1;
    // Stop generation at the worker, then stop playback. Speaker.stop() calls
    // onDone(reached) → finishSpeaking(interrupted=true), which queues the
    // generated-but-unspoken remainder. The not-yet-generated tail is cancelled
    // server-side so it never becomes garbage in the queue.
    this._interrupted = true;
    const heard = this.spokenCount;
    const heardText = heard > 0 ? this.sentences[heard - 1]?.text : undefined;
    this.replyHandle?.cancel({ heard, heardText });
    this.replyHandle = null;
    this.speaker?.stop();
  }
  private _interrupted = false;

  private finishSpeaking(reached: number, _normal: boolean) {
    const interrupted = this._interrupted;
    this._interrupted = false;
    this.speaker = null;
    // Leave barge mode; normal-sensitivity VAD for the next user utterance.
    this.mic.setBargeMode(false);
    this.mic.setOutputProbe(() => 0);
    // Everything up to `reached` was heard; the rest is the un-said remainder.
    const heard = Math.max(0, Math.min(reached, this.sentences.length));
    if (interrupted && heard < this.sentences.length) {
      const remainder = this.sentences.slice(heard).map((s) => s.text);
      const text = remainder.join(" ").trim();
      if (text) {
        const topic = remainder[0].slice(0, 60) + (remainder[0].length > 60 ? "…" : "");
        this.queue = [...this.queue, { id: `t${++_tid}`, topic, text, from: "interrupt" }];
      }
    }
    this.cfg.onPrefsSignal?.(this.turnCount ? this.interrupts / this.turnCount : 0);
    // After a barge-in on the server path the user is already talking → start
    // capturing that utterance immediately. Browser/native STT re-arm normally.
    if (interrupted && this.sttProvider === "server") {
      this.mic.startRecording();
      this.capturing = true;
      this.set("listening");
    } else {
      this.armListen();
    }
  }

  // ---- background-thread queue --------------------------------------------
  offerThread(id: string) {
    const t = this.queue.find((q) => q.id === id);
    if (!t) return;
    this.queue = this.queue.filter((q) => q.id !== id);
    this._interrupted = true;
    this.replyHandle?.cancel();
    this.replyHandle = null;
    this.speaker?.stop();
    this.browserAbort?.();
    this.speakLocal(t.text);
  }

  // Speak an already-known piece of text (a resumed background thread) without a
  // network turn — reuses the streaming speaker path by feeding it all at once.
  private speakLocal(text: string) {
    this.emitted = 0;
    this.sentences = [];
    this.spokenCount = 0;
    this._interrupted = false;
    this.tEndpoint = performance.now();
    this.lastTurnMs = null;
    if (!this.ttsProvider) {
      this.set("thinking");
      this.pushSentences(text, true);
      this.finishSpeaking(this.sentences.length, false);
      return;
    }
    this.speaker = this.makeSpeaker();
    const sp = this.speaker;
    this.mic.setOutputProbe(() => (sp.outputLevel ? sp.outputLevel() : 0));
    this.mic.setBargeMode(true);
    const ev = {
      onSentenceStart: (i: number) => {
        if (this.lastTurnMs === null) this.lastTurnMs = Math.round(performance.now() - this.tEndpoint);
        this.sentences[i] = { ...this.sentences[i], status: "speaking" as SentenceStatus };
        this.spokenCount = i; this.emit();
      },
      onSentenceEnd: (i: number) => {
        this.sentences[i] = { ...this.sentences[i], status: "heard" as SentenceStatus };
        this.spokenCount = i + 1; this.emit();
      },
      onDone: (reached: number) => this.finishSpeaking(reached, false),
    };
    if (sp.begin) sp.begin(ev); else sp.speak([], ev);
    this.pushSentences(text, true);
    if (sp.end) sp.end();
  }
  dismissThread(id: string) {
    this.queue = this.queue.filter((q) => q.id !== id);
    this.emit();
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
}
