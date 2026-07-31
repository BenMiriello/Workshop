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

import { hasCapability, native } from "../native/bridge";

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
    // Bound the probe (D6/Phase 6.3): a hung backend must not wedge the session
    // in "connecting". On timeout the fetch rejects, we return null, and
    // detectCaps falls to the browser/native tiers.
    const r = await fetch("/api/voice/health", { signal: AbortSignal.timeout(1500) });
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
  const nativeSpeech = hasCapability("speech");
  // Preference order: native (on device, private) → server (quality) → browser.
  let stt: SttProvider | null = null;
  if (nativeSpeech) stt = "native";
  else if (server?.stt.available) stt = "server";
  else if (browserStt) stt = "browser";
  let tts: TtsProvider | null = null;
  if (nativeSpeech) tts = "native";
  else if (server?.tts.available) tts = "server";
  else if (browserTts) tts = "browser";
  return { server, browserStt, browserTts, native: nativeSpeech, stt, tts };
}

// ---- audio unlock (autoplay policy) ----------------------------------------
// A one-frame silent WAV, played (muted) once from a user gesture so the browser
// treats later programmatic Audio().play() calls as user-initiated. Data URI so
// it is self-contained and needs no network under the artifact/CSP model.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRmQBAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YUABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==";
// Idempotent: the silent prime only needs to run once per page load.
let _audioPrimed = false;

// Called SYNCHRONOUSLY from the voice-toggle click (the user gesture). It (1)
// creates/resumes the shared ServerSpeaker AudioContext and (2) plays a silent
// clip so a later held-draft release — which fires OUTSIDE any gesture — is not
// blocked by the autoplay policy (D6). Calling this from an effect would not
// satisfy the gesture requirement, so it must live in the click handler.
export function unlockAudio(): void {
  ServerSpeaker.primeContext();
  if (_audioPrimed) return;
  try {
    const a = new Audio(SILENT_WAV);
    a.muted = true;
    const p = a.play();
    if (p && typeof p.then === "function") p.then(() => { try { a.pause(); } catch { /* noop */ } }).catch(() => {});
    _audioPrimed = true;
  } catch { /* noop — best-effort; real confirmation is on-device */ }
}

// ============================================================================
// Capture preflight — say WHY, not "mic error"
// ============================================================================
// Every distinct reason the input lane cannot run, as a value the UI can explain.
// "notAllowed" is a real permission denial; it is NOT what deliberately muting
// the mic produces (see VoiceSession.micSuspended).
export type MicFailKind =
  | "insecure"     // not an https/localhost origin — mediaDevices doesn't exist
  | "unsupported"  // secure, but the browser has no getUserMedia at all
  | "notAllowed"   // the user (or policy) denied permission
  | "notFound"     // no input device
  | "inUse"        // device held by another app (NotReadableError/TrackStartError)
  | "unknown";

export class MicError extends Error {
  kind: MicFailKind;
  constructor(kind: MicFailKind, message: string) {
    super(message);
    this.name = "MicError";
    this.kind = kind;
  }
}

// Static check, safe to run before any user gesture. Returns the blocking
// problem or null. getUserMedia is only defined on secure origins, so an http://
// tailnet or a WebView without the right host wiring lands here instead of
// throwing an opaque TypeError from inside MicStream.start().
export function micPreflight(): MicError | null {
  if (typeof window === "undefined") return new MicError("unsupported", "No browser environment.");
  if (!window.isSecureContext) {
    return new MicError(
      "insecure",
      `Voice input needs a secure origin (https or localhost). This page is ${window.location.protocol}//${window.location.host}.`,
    );
  }
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    return new MicError("unsupported", "This browser has no microphone capture API.");
  }
  return null;
}

// Map a getUserMedia rejection to a MicError with a recovery hint.
function classifyGumError(e: unknown): MicError {
  const name = (e as { name?: string })?.name || "";
  const msg = String((e as Error)?.message || e);
  if (name === "NotAllowedError" || name === "SecurityError" || /permission|not allowed/i.test(msg)) {
    return new MicError("notAllowed", "Microphone permission denied. Allow it in the browser's site settings, then turn voice on again.");
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return new MicError("notFound", "No microphone found. Connect an input device and try again.");
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return new MicError("inUse", "The microphone is in use by another app.");
  }
  return new MicError("unknown", `Could not open the microphone: ${msg}`);
}

// ============================================================================
// Microphone stream + voice-activity detection (VAD)
// ============================================================================
// One shared mic capture that (a) exposes a live audio level for the UI and for
// barge-in detection, and (b) records segments to a Blob for server STT.
export type MicEvents = {
  // net = level after subtracting the TTS envelope (what the VAD acts on).
  // raw = the unprocessed mic level. The UI needs BOTH: net drives the orb halo,
  // raw is the only thing that can answer "is this microphone even connected".
  onLevel?: (rms: number, raw?: number) => void;      // 0..1, ~60fps
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  // An earlier, lower-confidence pause (< endSilenceMs). Fires once per silence
  // gap when eager endpointing is on; the session may speculate on it.
  onEagerPause?: () => void;
  // A recorder-construction failure (e.g. no supported MediaRecorder mime). The
  // session surfaces it via snapshot.error instead of letting it throw uncaught.
  onError?: (message: string) => void;
};

// MediaRecorder container/codec candidates, most-preferred first. Safari has no
// audio/webm at all (D6): constructing MediaRecorder with an unsupported mime
// throws, which crashed the server-STT auto pick on iPhones. We negotiate the
// first supported type and fall back to the browser default when none match.
const REC_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
];

function pickRecorderMime(): string {
  const MR = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder;
  if (MR && typeof MR.isTypeSupported === "function") {
    for (const m of REC_MIME_CANDIDATES) {
      try { if (MR.isTypeSupported(m)) return m; } catch { /* keep scanning */ }
    }
  }
  return "";   // let MediaRecorder choose its own supported default (Safari: mp4)
}

// Map a recorder mime to a filename extension whisper/faster-whisper recognizes
// (PyAV decodes by container content, but a matching extension keeps the upload
// honest). Chrome yields webm/opus → .webm; Safari yields mp4/aac → .m4a.
function extForMime(mime: string): string {
  const m = (mime || "").toLowerCase();
  if (m.includes("mp4") || m.includes("m4a") || m.includes("aac")) return "m4a";
  if (m.includes("ogg")) return "ogg";
  if (m.includes("wav")) return "wav";
  return "webm";
}

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

  // Echo-aware barge-in: while the assistant is speaking, the mic also hears the
  // TTS. We subtract the TTS output envelope (outputProbe) from the mic level and
  // require the *net* signal to clear an elevated bar for a sustained window, so
  // residual echo doesn't count as the user interrupting. When nothing is
  // playing outputProbe()=0 and this is a plain VAD.
  private outputProbe: () => number = () => 0;
  private echoK = 1.6;             // how strongly to subtract the TTS envelope
  private bargeMode = false;       // true only while TTS is speaking
  private onsetSince = 0;
  private bargeSustainMs = 180;

  // ---- level telemetry + noise-floor calibration ---------------------------
  // The old fixed 0.06 onset threshold was a guess made against an unknown
  // capture chain (echoCancellation + noiseSuppression + AGC all move the
  // level). On a mic that never reached it, onSpeechStart never fired, so no
  // segment was ever cut and voice mode was silently, permanently deaf. The
  // threshold is now a MEASUREMENT: sample the ambient floor, sit above it.
  private sensitivity = 0.5;       // the user's knob, 0..1
  private noiseFloor = 0;          // measured ambient RMS
  private calibrating = false;
  private calStart = 0;
  private calPeak = 0;
  private calSum = 0;
  private calN = 0;
  private calibrated = false;
  private rawLevel = 0;            // last raw (pre-subtraction) mic level
  private rawPeak = 0;             // rolling peak, for the no-signal watchdog
  private static CAL_MS = 1200;

  setSensitivity(v: number) {
    this.sensitivity = Math.max(0, Math.min(1, v));
    this.applyThreshold();
  }

  // sensitivity 0..1 → a floor between 0.10 (insensitive) and 0.015 (hair
  // trigger). The live threshold is whichever is higher: that floor, or a
  // margin over the measured ambient noise.
  private applyThreshold() {
    const knobFloor = 0.10 - this.sensitivity * 0.085;
    this.startThreshold = this.calibrated
      ? Math.max(knobFloor, this.noiseFloor * 2.5)
      : knobFloor;
  }

  // Begin (or restart) a noise-floor measurement. Called when the input lane
  // arms, so a mid-session device change re-measures.
  calibrate() {
    this.calibrating = true;
    this.calStart = performance.now();
    this.calPeak = 0;
    this.calSum = 0;
    this.calN = 0;
  }

  private tickCalibration(rms: number, now: number) {
    this.calSum += rms;
    this.calN += 1;
    if (rms > this.calPeak) this.calPeak = rms;
    if (now - this.calStart < MicStream.CAL_MS) return;
    // Use the mean, not the peak: a single cough during calibration should not
    // deafen the session for the rest of it.
    const mean = this.calN ? this.calSum / this.calN : 0;
    this.noiseFloor = mean;
    this.calibrated = true;
    this.calibrating = false;
    this.applyThreshold();
  }

  // Live diagnostics for the settings sheet + the watchdogs. Everything the
  // question "why isn't it hearing me" needs, in one object.
  levels(): { raw: number; peak: number; floor: number; threshold: number; calibrated: boolean } {
    return {
      raw: this.rawLevel,
      peak: this.rawPeak,
      floor: this.noiseFloor,
      threshold: this.startThreshold,
      calibrated: this.calibrated,
    };
  }

  // Is the VAD currently inside an utterance? The preview loop only spends a
  // transcription pass when there is speech to preview.
  get inSpeech(): boolean { return this.speaking; }

  // Which input device the browser actually gave us. Not necessarily the one the
  // OS shows as default, which is a common source of "it works everywhere else".
  get deviceLabel(): string | null {
    return this.stream?.getAudioTracks()[0]?.label || null;
  }

  // Reset the rolling peak and report what it was. The no-signal watchdog calls
  // this on an interval: a peak of ~0 over a whole window means the track is
  // producing silence (unplugged, muted in the OS, or a dead WebView capture).
  takePeak(): number {
    const p = this.rawPeak;
    this.rawPeak = 0;
    return p;
  }

  // Trailing silence (ms) that ends an utterance on the server VAD path.
  // Operator-configurable ("Pause before reply"); clamped to a sane range.
  setEndSilence(ms: number) {
    this.endSilenceMs = Math.max(300, Math.min(2000, Math.round(ms)));
  }

  // Eager pause: an earlier silence tick for speculative endpointing. Kept safely
  // below the real endpoint so the two never collide.
  private eagerSilenceMs = 400;
  private eagerEnabled = false;
  private eagerFired = false;   // once per silence gap
  setEager(enabled: boolean, ms: number) {
    this.eagerEnabled = enabled;
    this.eagerSilenceMs = Math.max(200, Math.min(this.endSilenceMs - 100, Math.round(ms)));
  }

  // Provide a live 0..1 estimate of the TTS output level (server speaker only —
  // SpeechSynthesis can't be measured, so it stays 0 and we lean on the mic's
  // own echoCancellation + the elevated barge threshold).
  setOutputProbe(fn: () => number) { this.outputProbe = fn; }
  setBargeMode(on: boolean) { this.bargeMode = on; if (!on) this.onsetSince = 0; }

  async start(events: MicEvents = {}): Promise<void> {
    if (this.running) {
      this.events = events;
      return;
    }
    this.events = events;
    // Preflight first so an insecure origin or a capture-less browser reports
    // the actual reason instead of throwing "cannot read getUserMedia of
    // undefined" from the next line.
    const pre = micPreflight();
    if (pre) throw pre;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      throw classifyGumError(e);
    }
    this.ctx = new AudioContext();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    src.connect(this.analyser);
    this.running = true;
    this.rawPeak = 0;
    this.calibrate();
    this.loop();
  }

  private loop = () => {
    if (!this.analyser) return;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    // Net = mic energy minus the TTS we're currently emitting.
    const net = Math.max(0, rms - this.outputProbe() * this.echoK);
    const now = performance.now();
    this.rawLevel = rms;
    if (rms > this.rawPeak) this.rawPeak = rms;
    this.events.onLevel?.(Math.min(1, net * 4), Math.min(1, rms * 4));
    // While calibrating, measure the room and take no VAD decisions: the ambient
    // sample must not be polluted by treating it as speech.
    if (this.calibrating) {
      this.tickCalibration(rms, now);
      this.raf = requestAnimationFrame(this.loop);
      return;
    }
    const thr = this.bargeMode ? this.startThreshold * 1.8 : this.startThreshold;
    if (net > thr) {
      if (!this.speaking) {
        if (this.bargeMode) {
          // Require sustained speech to interrupt (residual echo is bursty).
          if (!this.onsetSince) this.onsetSince = now;
          else if (now - this.onsetSince > this.bargeSustainMs) {
            this.speaking = true;
            this.onsetSince = 0;
            this.events.onSpeechStart?.();
          }
        } else {
          this.speaking = true;
          this.events.onSpeechStart?.();
        }
      }
      this.silenceSince = 0;
      this.eagerFired = false;
    } else {
      this.onsetSince = 0;
      if (this.speaking) {
        if (!this.silenceSince) this.silenceSince = now;
        else {
          const gap = now - this.silenceSince;
          if (this.eagerEnabled && !this.eagerFired && !this.bargeMode &&
              gap > this.eagerSilenceMs && this.eagerSilenceMs < this.endSilenceMs) {
            this.eagerFired = true;
            this.events.onEagerPause?.();
          }
          if (gap > this.endSilenceMs) {
            this.speaking = false;
            this.silenceSince = 0;
            this.eagerFired = false;
            this.events.onSpeechEnd?.();
          }
        }
      }
    }
    this.raf = requestAnimationFrame(this.loop);
  };

  // Record a segment to a Blob for server STT. The container/codec is negotiated
  // (see pickRecorderMime) so this never throws on Safari; a construction failure
  // is reported via onError instead of propagating uncaught (D6).
  startRecording() {
    if (!this.stream) return;
    this.chunks = [];
    try {
      const mime = pickRecorderMime();
      const chunks = this.chunks;
      this.recorder = mime
        ? new MediaRecorder(this.stream, { mimeType: mime })
        : new MediaRecorder(this.stream);
      // Bind to THIS chunk array so a discarded/superseded recorder's late
      // ondataavailable can never leak into a fresh segment's chunks.
      this.recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      this.recorder.start();
    } catch (e) {
      this.recorder = null;
      this.events.onError?.(`recorder: ${String((e as Error)?.message || e)}`);
    }
  }

  // Barge pre-roll (Phase 6.4, self-contained + revertable): the recorder runs
  // continuously through the assistant's speech so an interruption is captured
  // from its first word. This drops the current recorder's buffer (assistant TTS
  // echo + the sustain window) and starts a fresh one hot, with no cold-start
  // gap. Detaching the old ondataavailable first prevents its trailing data from
  // contaminating the new segment. Revert this + its callers to disable pre-roll.
  discardSegment() {
    const old = this.recorder;
    if (old) { old.ondataavailable = null; try { old.stop(); } catch { /* noop */ } }
    this.recorder = null;
    this.startRecording();
  }

  // A decodable copy of the CURRENT segment so far, without ending it.
  //
  // This is what makes live partials possible on the server path. requestData()
  // flushes everything buffered since the segment started into ondataavailable;
  // because the chunks run from the segment's first byte, they form a complete
  // container (header included) that PyAV can decode. Chopping the stream into
  // independent slices would not — only the first slice would carry a header,
  // and re-cutting mid-word wrecks accuracy at the seams.
  //
  // The segment keeps recording throughout, so the authoritative transcript at
  // the endpoint is still ONE clean pass over the whole utterance.
  async snapshot(): Promise<Blob | null> {
    const rec = this.recorder;
    if (!rec || rec.state !== "recording") return null;
    const chunks = this.chunks;
    return new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        rec.removeEventListener("dataavailable", onData);
        resolve(chunks.length ? new Blob([...chunks], { type: rec.mimeType }) : null);
      };
      const onData = () => { queueMicrotask(done); };
      rec.addEventListener("dataavailable", onData);
      // Don't hang the preview loop if the recorder never answers.
      setTimeout(done, 600);
      try { rec.requestData(); } catch { done(); }
    });
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

  // Close the current segment and immediately start a new one — used to cut an
  // utterance at a pause (eager or endpoint) without dropping the audio that
  // follows. The seam always falls inside real silence, so no mid-word cut.
  async cutSegment(): Promise<Blob | null> {
    const blob = await this.stopRecording();
    this.startRecording();
    return blob;
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

// ---- STT: native (iOS on-device via the shell bridge) ----------------------
export async function nativeTranscribe(locale = "en-US"): Promise<string> {
  const n = native();
  if (!n || !n.has("speech")) throw new Error("no native speech");
  const r = await n.speech.transcribe({ locale, onDevice: true });
  return (r?.text || "").trim();
}

// ---- STT: server ------------------------------------------------------------
export async function transcribeBlob(
  blob: Blob,
  opts?: { preview?: boolean; signal?: AbortSignal },
): Promise<string> {
  const fd = new FormData();
  // Filename extension tracks the negotiated recorder mime (webm on Chrome, m4a
  // on Safari); appending a Blob sets the part's Content-Type from blob.type.
  fd.append("audio", blob, `utterance.${extForMime(blob.type)}`);
  fd.append("language", "en");
  // Display-only pass over a still-growing utterance: smaller model, no VAD
  // trim. Never becomes the committed turn text.
  if (opts?.preview) fd.append("preview", "1");
  const r = await fetch("/api/voice/stt", { method: "POST", body: fd, signal: opts?.signal });
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
  let aborted = false;
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
    // `aborted` is what the recognizer reports when WE tore it down (muting the
    // mic, ending the session). It is benign and must never be confused with a
    // permission denial — conflating the two is what made muting the mic read as
    // "microphone not allowed" and kill the session. Resolve with whatever text
    // we had instead of rejecting.
    rec.onerror = (e: any) => {
      const code = e?.error || "recognition error";
      if (aborted || code === "aborted") { resolve(""); return; }
      reject(new Error(code));
    };
    rec.onend = () => resolve(aborted ? "" : finalText.trim());
    try { rec.start(); } catch (e) { reject(e as Error); }
  });
  // Discard, don't finalize: an abort means the caller no longer wants this
  // utterance (mic muted, session ended, barge superseded it), so it must not
  // come back as text and start a turn.
  return {
    promise,
    abort: () => {
      aborted = true;
      try { rec.abort(); } catch { try { rec.stop(); } catch { /* noop */ } }
    },
  };
}

// ============================================================================
// TTS playback — sentence by sentence, interruptible, with per-sentence events
// ============================================================================
export type SpeakEvents = {
  onSentenceStart?: (i: number) => void;
  onSentenceEnd?: (i: number) => void;
  onDone?: (reached: number) => void;   // index reached when finished/stopped
  // Autoplay policy blocked a programmatic play() (a held-draft release happens
  // outside a user gesture). The session surfaces a "tap to enable audio" hint
  // via snapshot.error rather than silently marking the sentence heard (D6).
  onBlocked?: () => void;
};

export interface Speaker {
  speak(sentences: string[], ev: SpeakEvents): void;
  // Streaming API: begin a turn, append sentences as the reply generates, end
  // when the reply is complete. Sentences render ahead of playback so there is
  // no dead air between them. Implemented by ServerSpeaker; optional elsewhere
  // (callers fall back to speak() when begin is absent).
  begin?(ev: SpeakEvents): void;
  enqueue?(sentence: string): void;
  end?(): void;
  // Held: render/queue ahead but do not start playback until released. Used for
  // speculative drafts. Absent on speakers that cannot hold.
  setHeld?(on: boolean): void;
  // Null the event sink so a subsequent stop() cannot fire onDone/onSentence*
  // (used by the session to tear a superseded/barged/timed-out speaker down
  // without accidentally driving finishSpeaking).
  detach(): void;
  stop(): void;             // returns via onDone(reached)
  readonly speaking: boolean;
  outputLevel?(): number;   // live 0..1 TTS envelope, when measurable
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

  // Shared WebAudio graph so we can read the live output envelope (fed to the
  // mic's echo-aware barge-in). One AudioContext for the app; a fresh source per
  // <audio> element routed through one analyser → destination.
  private static actx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private blockedNotified = false;   // fire onBlocked at most once per turn

  constructor(opts: { voice?: string; speed?: number } = {}) {
    this.voice = opts.voice;
    this.speed = opts.speed ?? 1.0;
  }

  // Create/resume the shared AudioContext. Called from unlockAudio() inside the
  // voice-toggle click so the graph is live before the first held-draft release.
  static primeContext() {
    try {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!ServerSpeaker.actx) ServerSpeaker.actx = new AC();
      const ctx = ServerSpeaker.actx;
      if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    } catch { /* noop — outputLevel just returns 0 */ }
  }

  private wireGraph(a: HTMLAudioElement) {
    try {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!ServerSpeaker.actx) ServerSpeaker.actx = new AC();
      const ctx = ServerSpeaker.actx;
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      if (!this.analyser) {
        this.analyser = ctx.createAnalyser();
        this.analyser.fftSize = 512;
        this.analyser.connect(ctx.destination);
      }
      const src = ctx.createMediaElementSource(a);
      src.connect(this.analyser);
    } catch {
      // createMediaElementSource unavailable → element still plays natively;
      // outputLevel() just returns 0 and we lean on echoCancellation.
    }
  }

  outputLevel(): number {
    if (!this.analyser) return 0;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    return Math.sqrt(s / buf.length);
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

  // Render promise per queued sentence — started at enqueue time, so audio for
  // sentence i+1 is already in flight while i plays (no per-sentence round-trip
  // of dead air). `ended` marks that no more sentences will be appended.
  private renders: (Promise<string | null>)[] = [];
  private ended = false;
  private playing = false;
  private held = false;

  begin(ev: SpeakEvents) {
    this.queue = [];
    this.renders = [];
    this.idx = 0;
    this.ev = ev;
    this.stopped = false;
    this.done = false;
    this.ended = false;
    this.playing = false;
    this.speaking = true;
    this.blockedNotified = false;
  }

  // Held: keep rendering ahead (audio is fetched at enqueue) but do not start
  // playback until released. Used for speculative drafts (generate + pre-render
  // during the pause, speak only once the turn is confirmed).
  setHeld(on: boolean) {
    this.held = on;
    if (!on) void this.pump();
  }

  enqueue(sentence: string) {
    if (this.stopped || this.ended) return;
    this.queue.push(sentence);
    this.renders.push(this.render(sentence).catch(() => null));
    void this.pump();
  }

  end() {
    this.ended = true;
    void this.pump();
  }

  speak(sentences: string[], ev: SpeakEvents) {
    this.begin(ev);
    for (const s of sentences) this.enqueue(s);
    this.end();
  }

  private async pump() {
    if (this.playing || this.stopped || this.held) return;
    const i = this.idx;
    if (i >= this.queue.length) {
      if (this.ended) this.finish(i);
      return;
    }
    this.playing = true;
    const url = await this.renders[i];
    if (this.stopped) { if (url) URL.revokeObjectURL(url); this.playing = false; this.finish(i); return; }
    if (!url) { this.playing = false; this.idx = i + 1; void this.pump(); return; }
    const a = new Audio(url);
    this.audio = a;
    this.wireGraph(a);
    this.ev.onSentenceStart?.(i);
    const advance = () => {
      URL.revokeObjectURL(url);
      this.playing = false;
      this.idx = i + 1;
      void this.pump();
    };
    a.onended = () => { this.ev.onSentenceEnd?.(i); advance(); };
    a.onerror = () => advance();
    a.play().catch((err: unknown) => {
      // Autoplay policy blocks programmatic play() outside a user gesture — this
      // is the held-draft release path (D6). Surface a hint once via onBlocked
      // instead of silently marking the sentence heard; still advance so the turn
      // completes rather than wedging in "speaking". unlockAudio() (primed from
      // the toggle click) is what prevents this on a well-behaved device.
      if ((err as { name?: string })?.name === "NotAllowedError" && !this.blockedNotified) {
        this.blockedNotified = true;
        this.ev.onBlocked?.();
      }
      advance();
    });
  }

  // `reached` = count of sentences fully heard before this point (== the index
  // of the sentence in flight when interrupted).
  private finish(reached: number) {
    if (this.done) return;
    this.done = true;
    this.speaking = false;
    this.ev.onDone?.(reached);
  }

  detach() { this.ev = {}; }

  stop() {
    this.stopped = true;
    if (this.audio) { try { this.audio.pause(); } catch { /* noop */ } this.audio = null; }
    this.finish(this.idx);
  }
}

// Browser TTS via SpeechSynthesis — a holdable, streaming sentence queue.
export class BrowserSpeaker implements Speaker {
  private stopped = false;
  private done = false;
  private idx = 0;
  private queue: string[] = [];
  private ended = false;
  private held = false;
  private busy = false;
  private ev: SpeakEvents = {};
  private voiceName?: string;
  private rate = 1.0;
  speaking = false;

  constructor(opts: { voiceName?: string; rate?: number } = {}) {
    this.voiceName = opts.voiceName;
    this.rate = opts.rate ?? 1.0;
  }

  begin(ev: SpeakEvents) {
    this.ev = ev;
    this.stopped = false;
    this.done = false;
    this.idx = 0;
    this.queue = [];
    this.ended = false;
    this.held = false;
    this.busy = false;
    this.speaking = true;
  }
  enqueue(sentence: string) {
    if (this.stopped || this.ended) return;
    this.queue.push(sentence);
    this.tick();
  }
  end() { this.ended = true; this.tick(); }
  setHeld(on: boolean) { this.held = on; if (!on) this.tick(); }

  speak(sentences: string[], ev: SpeakEvents) {
    this.begin(ev);
    for (const s of sentences) this.enqueue(s);
    this.end();
  }

  private tick() {
    if (this.busy || this.stopped || this.held) return;
    const i = this.idx;
    if (i >= this.queue.length) { if (this.ended) this.finish(i); return; }
    const u = new SpeechSynthesisUtterance(this.queue[i]);
    const voices = window.speechSynthesis.getVoices();
    const v = this.voiceName ? voices.find((x) => x.name === this.voiceName) : undefined;
    if (v) u.voice = v;
    u.rate = this.rate;
    this.busy = true;
    u.onstart = () => this.ev.onSentenceStart?.(i);
    u.onend = () => { if (this.stopped) return; this.ev.onSentenceEnd?.(i); this.busy = false; this.idx = i + 1; this.tick(); };
    u.onerror = () => { if (this.stopped) return; this.busy = false; this.idx = i + 1; this.tick(); };
    window.speechSynthesis.speak(u);
  }

  private finish(reached: number) {
    if (this.done) return;
    this.done = true;
    this.speaking = false;
    this.ev.onDone?.(reached);
  }

  detach() { this.ev = {}; }

  stop() {
    this.stopped = true;
    try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    this.finish(this.idx);
  }
}

// One-shot browser-TTS notice (e.g. the watchdog's spoken retry/failure notice).
// Always a standalone utterance — it never overlaps a Kokoro reply, so using the
// browser voice here does not reintroduce the mixed-voice bug.
export function speakNotice(text: string) {
  if (!browserTtsAvailable()) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    window.speechSynthesis.speak(u);
  } catch { /* noop */ }
}

// Native TTS via the shell's AVSpeechSynthesizer bridge, one sentence per call.
export class NativeSpeaker implements Speaker {
  private done = false;
  private stopped = false;
  private idx = 0;
  private queue: string[] = [];
  private ended = false;
  private held = false;
  private busy = false;
  private ev: SpeakEvents = {};
  private rate = 1.0;
  private voice?: string;
  speaking = false;

  constructor(opts: { voice?: string; speed?: number } = {}) {
    this.voice = opts.voice;
    this.rate = opts.speed ?? 1.0;
  }

  begin(ev: SpeakEvents) {
    this.ev = ev;
    this.done = false;
    this.stopped = false;
    this.idx = 0;
    this.queue = [];
    this.ended = false;
    this.held = false;
    this.busy = false;
    this.speaking = true;
  }
  enqueue(sentence: string) {
    if (this.stopped || this.ended) return;
    this.queue.push(sentence);
    this.tick();
  }
  end() { this.ended = true; this.tick(); }
  setHeld(on: boolean) { this.held = on; if (!on) this.tick(); }

  speak(sentences: string[], ev: SpeakEvents) {
    this.begin(ev);
    for (const s of sentences) this.enqueue(s);
    this.end();
  }

  private tick() {
    if (this.busy || this.stopped || this.held) return;
    const i = this.idx;
    if (i >= this.queue.length) { if (this.ended) this.finish(i); return; }
    const n = native();
    if (!n || !n.has("speech")) { this.finish(i); return; }
    this.busy = true;
    this.ev.onSentenceStart?.(i);
    n.speech
      .speak(this.queue[i], { rate: this.rate, voice: this.voice })
      .then(() => { if (this.stopped) return; this.ev.onSentenceEnd?.(i); this.busy = false; this.idx = i + 1; this.tick(); })
      .catch(() => { if (this.stopped) return; this.busy = false; this.idx = i + 1; this.tick(); });
  }

  private finish(reached: number) {
    if (this.done) return;
    this.done = true;
    this.speaking = false;
    this.ev.onDone?.(reached);
  }

  detach() { this.ev = {}; }

  stop() {
    this.stopped = true;
    native()?.speech.stop().catch(() => {});
    this.finish(this.idx);
  }
}

export function makeSpeaker(tts: TtsProvider, opts: { voice?: string; speed?: number } = {}): Speaker {
  if (tts === "server") return new ServerSpeaker(opts);
  if (tts === "native") return new NativeSpeaker(opts);
  return new BrowserSpeaker({ voiceName: opts.voice, rate: opts.speed });
}

// Coarse sentence split tuned for speech; merges very short fragments.
export function splitSentences(text: string): string[] {
  const parts = (text.match(/[^.!?…\n]+[.!?…]*/g) || []).map((s) => s.trim()).filter(Boolean);
  const out: string[] = [];
  for (const p of parts) {
    if (out.length && p.length < 24) out[out.length - 1] = `${out[out.length - 1]} ${p}`;
    else out.push(p);
  }
  return out.length ? out : [text.trim()].filter(Boolean);
}

// Strip markdown/code/URLs so TTS reads prose, not punctuation.
export function cleanForSpeech(text: string): string {
  return (text || "")
    .replace(/```[\s\S]*?```/g, " (code) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " link ")
    .replace(/[_>#|]/g, "")
    .replace(/\n{2,}/g, ". ")
    .replace(/\s+/g, " ")
    .trim();
}
