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
  barge_in: boolean;
  barge_sensitivity: number;
  verbosity: "terse" | "normal";
};

export const DEFAULT_PREFS: VoicePrefs = {
  voice: "af_heart",
  speed: 1.0,
  tts_provider: "auto",
  stt_provider: "auto",
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
};

export type VoiceConfig = {
  // Send a user utterance through the chat backend, resolve with the reply text.
  sendTurn: (text: string) => Promise<string>;
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
    if (p.tts_provider === "off") this.ttsProvider = null;
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
  private async onUtterance(raw: string) {
    const text = (raw || "").trim();
    this.partial = "";
    if (!text) { this.armListen(); return; }
    this.lastUser = text;
    this.turnCount += 1;
    this.set("thinking");
    // Cold-start filler so there's no dead air while a server session warms up.
    if (this.turnCount === 1 && this.ttsProvider === "server") speakFiller("One moment.");
    let reply = "";
    try {
      reply = await this.cfg.sendTurn(text);
    } catch (e) {
      this.error = `reply: ${String(e)}`;
      this.emit();
      this.armListen();
      return;
    }
    this.speakReply(reply);
  }

  // ---- speaking ------------------------------------------------------------
  private extractAsides(reply: string): { body: string; asides: QueuedThread[] } {
    const asides: QueuedThread[] = [];
    const body = reply.replace(ASIDE_RE, (_m, topic) => {
      asides.push({ id: `t${++_tid}`, topic: String(topic).trim(), text: String(topic).trim(), from: "aside" });
      return "";
    }).trim();
    return { body, asides };
  }

  private speakReply(reply: string) {
    const { body, asides } = this.extractAsides(reply);
    if (asides.length) this.queue = [...this.queue, ...asides];
    const sents = splitSentences(cleanForSpeech(body));
    this.sentences = sents.map((t) => ({ text: t, status: "pending" as SentenceStatus }));
    this.spokenCount = 0;
    if (!sents.length || !this.ttsProvider) {
      // No TTS (muted) — treat as immediately "heard", go back to listening.
      this.sentences = this.sentences.map((s) => ({ ...s, status: "heard" }));
      this.spokenCount = this.sentences.length;
      this.armListen();
      return;
    }
    this.set("speaking");
    this.speaker = this.makeSpeaker();
    // Echo-aware barge-in: feed the mic the TTS output envelope (server speaker
    // only) and raise the interrupt bar while speaking.
    const sp = this.speaker;
    this.mic.setOutputProbe(() => (sp.outputLevel ? sp.outputLevel() : 0));
    this.mic.setBargeMode(true);
    this.speaker.speak(sents, {
      onSentenceStart: (i) => {
        this.sentences[i] = { ...this.sentences[i], status: "speaking" };
        this.spokenCount = i;
        this.emit();
      },
      onSentenceEnd: (i) => {
        this.sentences[i] = { ...this.sentences[i], status: "heard" };
        this.spokenCount = i + 1;
        this.emit();
      },
      onDone: (reached) => this.finishSpeaking(reached, false),
    });
  }

  private makeSpeaker(): Speaker {
    const p = this.cfg.prefs;
    if (this.ttsProvider === "server") return new ServerSpeaker({ voice: p.voice, speed: p.speed });
    if (this.ttsProvider === "native") return new NativeSpeaker({ speed: p.speed });
    return new BrowserSpeaker({ voiceName: undefined, rate: p.speed });
  }

  private bargeIn() {
    this.interrupts += 1;
    // Speaker.stop() calls onDone(reached) → finishSpeaking(interrupted=true).
    this._interrupted = true;
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
    this.speaker?.stop();
    this.browserAbort?.();
    this.speakReply(t.text);
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
