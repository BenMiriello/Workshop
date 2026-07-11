// Voice engine — the capture (STT) and playback (TTS) layer for voice mode.
//
// Two provider tiers, auto-selected, behind one small API:
//   * server  — POST /api/voice/stt (faster-whisper) + /api/voice/tts (Kokoro).
//               Best quality, fully local/private.
//   * browser — Web Speech API SpeechRecognition + SpeechSynthesis. Zero infra,
//               always available on mobile web; the fallback when the server
//               models aren't loaded.
//   * native  — the Expo shell's `audio` bridge capability (seam for later).
//
// Nothing here is React. The session state machine (voiceSession.ts) drives it.

import { hasCapability } from "../native/bridge";

export type SttProvider = "server" | "browser" | "native";
export type TtsProvider = "server" | "browser" | "native";

export type VoiceHealth = {
  stt: { available: boolean; model?: string };
  tts: { available: boolean; voice?: string; sample_rate?: number };
  device?: string;
};

export type EngineCaps = {
  server: VoiceHealth | null;
  browserStt: boolean;
  browserTts: boolean;
  native: boolean;
  stt: SttProvider | null;
  tts: TtsProvider | null;
};

// ---- browser capability probes ---------------------------------------------
type SR = typeof window & {
  SpeechRecognition?: any;
  webkitSpeechRecognition?: any;
};

export function browserSttAvailable(): boolean {
  const w = window as SR;
  return !!(w.SpeechRecognition || w.webkitSpeechRecognition);
}

export function browserTtsAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function getSR(): any | null {
  const w = window as SR;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

// ---- server probe ----------------------------------------------------------
export async function probeServer(): Promise<VoiceHealth | null> {
  try {
    const r = await fetch("/api/voice/health");
    if (!r.ok) return null;
    return (await r.json()) as VoiceHealth;
  } catch {
    return null;
  }
}

export async function detectCaps(): Promise<EngineCaps> {
  const server = await probeServer();
  const browserStt = browserSttAvailable();
  const browserTts = browserTtsAvailable();
  const nativeAudio = hasCapability("audio");
  // Preference order: native (on device) → server (quality) → browser (fallback).
  let stt: SttProvider | null = null;
  if (nativeAudio) stt = "native";
  else if (server?.stt.available) stt = "server";
  else if (browserStt) stt = "browser";
  let tts: TtsProvider | null = null;
  if (server?.tts.available) tts = "server";
  else if (browserTts) tts = "browser";
  return { server, browserStt, browserTts, native: nativeAudio, stt, tts };
}

// ============================================================================
// Microphone stream + voice-activity detection (VAD)
// ============================================================================
// One shared mic capture that (a) exposes a live audio level for the UI and for
// barge-in detection, and (b) records segments to a Blob for server STT.
export type MicEvents = {
  onLevel?: (rms: number) => void;      // 0..1, ~60fps
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
};

export class MicStream {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private raf = 0;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private speaking = false;
  private silenceSince = 0;
  private startThreshold = 0.06;   // RMS to count as speech onset
  private endSilenceMs = 900;      // trailing silence that ends an utterance
  private events: MicEvents = {};
  running = false;

  setSensitivity(v: number) {
    // v 0..1 → threshold 0.12 (insensitive) .. 0.02 (hair-trigger)
    this.startThreshold = 0.12 - Math.max(0, Math.min(1, v)) * 0.1;
  }

  async start(events: MicEvents = {}): Promise<void> {
    if (this.running) {
      this.events = events;
      return;
    }
    this.events = events;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    src.connect(this.analyser);
    this.running = true;
    this.loop();
  }

  private loop = () => {
    if (!this.analyser) return;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    this.events.onLevel?.(Math.min(1, rms * 4));
    const now = performance.now();
    if (rms > this.startThreshold) {
      if (!this.speaking) {
        this.speaking = true;
        this.events.onSpeechStart?.();
      }
      this.silenceSince = 0;
    } else if (this.speaking) {
      if (!this.silenceSince) this.silenceSince = now;
      else if (now - this.silenceSince > this.endSilenceMs) {
        this.speaking = false;
        this.silenceSince = 0;
        this.events.onSpeechEnd?.();
      }
    }
    this.raf = requestAnimationFrame(this.loop);
  };

  // Record a segment to a Blob (webm/opus) for server STT.
  startRecording() {
    if (!this.stream) return;
    this.chunks = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    this.recorder = new MediaRecorder(this.stream, { mimeType: mime });
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.recorder.start();
  }

  async stopRecording(): Promise<Blob | null> {
    const rec = this.recorder;
    if (!rec) return null;
    return new Promise((resolve) => {
      rec.onstop = () => resolve(new Blob(this.chunks, { type: rec.mimeType }));
      rec.stop();
      this.recorder = null;
    });
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    try { this.recorder?.stop(); } catch { /* noop */ }
    this.recorder = null;
    this.analyser?.disconnect();
    this.ctx?.close().catch(() => {});
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.ctx = null;
    this.analyser = null;
    this.speaking = false;
  }
}

// ---- STT: server ------------------------------------------------------------
export async function transcribeBlob(blob: Blob): Promise<string> {
  const fd = new FormData();
  fd.append("audio", blob, "utterance.webm");
  fd.append("language", "en");
  const r = await fetch("/api/voice/stt", { method: "POST", body: fd });
  if (!r.ok) throw new Error(`stt ${r.status}`);
  const j = await r.json();
  return (j.text || "").trim();
}

// ---- STT: browser (Web Speech API), one utterance -------------------------
// Resolves with the final transcript; calls onPartial with interim text.
export function recognizeOnce(onPartial?: (t: string) => void): {
  promise: Promise<string>;
  abort: () => void;
} {
  const rec = getSR();
  if (!rec) return { promise: Promise.reject(new Error("no SpeechRecognition")), abort: () => {} };
  rec.lang = "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  let finalText = "";
  const promise = new Promise<string>((resolve, reject) => {
    rec.onresult = (e: any) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else interim += t;
      }
      onPartial?.((finalText + " " + interim).trim());
    };
    rec.onerror = (e: any) => reject(new Error(e.error || "recognition error"));
    rec.onend = () => resolve(finalText.trim());
    try { rec.start(); } catch (e) { reject(e as Error); }
  });
  return { promise, abort: () => { try { rec.stop(); } catch { /* noop */ } } };
}

// ============================================================================
// TTS playback — sentence by sentence, interruptible, with per-sentence events
// ============================================================================
export type SpeakEvents = {
  onSentenceStart?: (i: number) => void;
  onSentenceEnd?: (i: number) => void;
  onDone?: (reached: number) => void;   // index reached when finished/stopped
};

export interface Speaker {
  speak(sentences: string[], ev: SpeakEvents): void;
  stop(): void;             // returns via onDone(reached)
  readonly speaking: boolean;
}

// Server TTS: fetch a WAV per sentence, play sequentially. Prefetches the next
// sentence's audio while the current one plays.
export class ServerSpeaker implements Speaker {
  private queue: string[] = [];
  private idx = 0;              // sentence index currently playing
  private audio: HTMLAudioElement | null = null;
  private stopped = false;
  private done = false;
  private ev: SpeakEvents = {};
  private voice?: string;
  private speed = 1.0;
  speaking = false;

  constructor(opts: { voice?: string; speed?: number } = {}) {
    this.voice = opts.voice;
    this.speed = opts.speed ?? 1.0;
  }

  private async render(text: string): Promise<string> {
    const r = await fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: this.voice, speed: this.speed }),
    });
    if (!r.ok) throw new Error(`tts ${r.status}`);
    const blob = await r.blob();
    return URL.createObjectURL(blob);
  }

  speak(sentences: string[], ev: SpeakEvents) {
    this.queue = sentences;
    this.idx = 0;
    this.ev = ev;
    this.stopped = false;
    this.done = false;
    this.speaking = true;
    this.playFrom(0);
  }

  private async playFrom(i: number) {
    this.idx = i;
    if (this.stopped || i >= this.queue.length) {
      this.finish(i);
      return;
    }
    let url: string;
    try {
      url = await this.render(this.queue[i]);
    } catch {
      this.playFrom(i + 1);
      return;
    }
    if (this.stopped) { URL.revokeObjectURL(url); this.finish(i); return; }
    const a = new Audio(url);
    this.audio = a;
    this.ev.onSentenceStart?.(i);
    a.onended = () => {
      URL.revokeObjectURL(url);
      this.ev.onSentenceEnd?.(i);
      this.playFrom(i + 1);
    };
    a.onerror = () => { URL.revokeObjectURL(url); this.playFrom(i + 1); };
    a.play().catch(() => this.playFrom(i + 1));
  }

  // `reached` = count of sentences fully heard before this point (== the index
  // of the sentence in flight when interrupted).
  private finish(reached: number) {
    if (this.done) return;
    this.done = true;
    this.speaking = false;
    this.ev.onDone?.(reached);
  }

  stop() {
    this.stopped = true;
    if (this.audio) { try { this.audio.pause(); } catch { /* noop */ } this.audio = null; }
    this.finish(this.idx);
  }
}

// Browser TTS via SpeechSynthesis — one utterance per sentence.
export class BrowserSpeaker implements Speaker {
  private stopped = false;
  private done = false;
  private idx = 0;
  private ev: SpeakEvents = {};
  private voiceName?: string;
  private rate = 1.0;
  speaking = false;

  constructor(opts: { voiceName?: string; rate?: number } = {}) {
    this.voiceName = opts.voiceName;
    this.rate = opts.rate ?? 1.0;
  }

  speak(sentences: string[], ev: SpeakEvents) {
    this.ev = ev;
    this.stopped = false;
    this.done = false;
    this.idx = 0;
    this.speaking = true;
    const synth = window.speechSynthesis;
    const voices = synth.getVoices();
    const v = this.voiceName ? voices.find((x) => x.name === this.voiceName) : undefined;
    let i = 0;
    const next = () => {
      this.idx = i;
      if (this.stopped || i >= sentences.length) { this.finish(i); return; }
      const u = new SpeechSynthesisUtterance(sentences[i]);
      if (v) u.voice = v;
      u.rate = this.rate;
      const idx = i;
      u.onstart = () => this.ev.onSentenceStart?.(idx);
      u.onend = () => { if (this.stopped) return; this.ev.onSentenceEnd?.(idx); i++; next(); };
      u.onerror = () => { if (this.stopped) return; i++; next(); };
      synth.speak(u);
    };
    next();
  }

  private finish(reached: number) {
    if (this.done) return;
    this.done = true;
    this.speaking = false;
    this.ev.onDone?.(reached);
  }

  stop() {
    this.stopped = true;
    try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    this.finish(this.idx);
  }
}

// Instant, always-available filler while a cold session warms up.
export function speakFiller(text = "One moment.") {
  if (!browserTtsAvailable()) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    window.speechSynthesis.speak(u);
  } catch { /* noop */ }
}

export function makeSpeaker(tts: TtsProvider, opts: { voice?: string; speed?: number } = {}): Speaker {
  if (tts === "server") return new ServerSpeaker(opts);
  return new BrowserSpeaker({ voiceName: opts.voice, rate: opts.speed });
}
