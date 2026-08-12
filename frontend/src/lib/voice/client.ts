// The voice client: a device plus a socket, and no conversation logic at all.
//
// Everything that used to live in `voiceSession.ts` (1600 lines, 47 instance
// fields, eight near-identical teardown blocks) is now server-side in
// `services/voice/machine.py`, as a pure reducer. What is left here is what
// genuinely belongs on the device: open the microphone, send frames, play the
// audio that comes back, report what was actually heard, and mirror the state
// the server pushes.
//
// That split is what makes the Expo app a client rather than a second
// implementation. This file is the whole contract, and it is small enough to
// port honestly.

import { MicDevice } from "./capture";
import { MicError, type MicFailKind } from "./errors";
import { SpeakerDevice } from "./playback";
import {
  KIND_MIC, KIND_TTS, decodeAudio, encodeAudio,
  type ClauseStatus, type ServerMessage, type ServerState, type VoicePhase,
} from "./protocol";
import { VoiceSocket, streamUrl } from "./transport";
import type { VoicePrefs } from "./modes";

export type SpokenSentence = { text: string; status: ClauseStatus };

export type VoiceSnapshot = {
  state: VoicePhase;
  partial: string;
  lastUser: string;
  sentences: SpokenSentence[];
  spokenCount: number;
  error: string | null;
  /** Unrecoverable for this configuration (mic denied with a voice-only lane). */
  fatal: boolean;
  interruptRate: number;
  pendingUser: string;
  micFail: MicFailKind | null;
  /** A live caption is catching up. */
  previewing: boolean;
  connected: boolean;
  /** False when the loopback echo canceller could not be established. */
  echoCancelled: boolean;
  tiers: { vad: string; turnEnd: string };
  engines: { stt: boolean; tts: boolean; sttModel: string; ttsVoice: string };
};

const EMPTY: VoiceSnapshot = {
  state: "idle", partial: "", lastUser: "", sentences: [], spokenCount: 0,
  error: null, fatal: false, interruptRate: 0, pendingUser: "", micFail: null,
  previewing: false, connected: false, echoCancelled: false,
  tiers: { vad: "", turnEnd: "" },
  engines: { stt: false, tts: false, sttModel: "", ttsVoice: "" },
};

export type ClientConfig = {
  contextKey: string;
  model?: string;
  threadId?: string | null;
  prefs: VoicePrefs;
  onThreadBound?: (id: string) => void;
};

export class VoiceClient {
  private mic = new MicDevice();
  private speaker = new SpeakerDevice();
  private sock: VoiceSocket | null = null;
  private cfg: ClientConfig;
  private listeners = new Set<() => void>();
  private snap: VoiceSnapshot = EMPTY;
  private onLevelCb: ((v: number) => void) | null = null;
  /** Server clause token -> the turn and index it belongs to. */
  private tokens = new Map<number, { turn: string; index: number }>();
  private starting = false;

  constructor(cfg: ClientConfig) {
    this.cfg = cfg;
  }

  // ---- store -------------------------------------------------------------
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  snapshot(): VoiceSnapshot { return this.snap; }
  private patch(p: Partial<VoiceSnapshot>) {
    this.snap = { ...this.snap, ...p };
    for (const fn of this.listeners) fn();
  }
  /** Mic level for the orb. Deliberately NOT in the snapshot: it changes ~50fps
   *  and would re-render the tree every frame. */
  setOnLevel(cb: ((v: number) => void) | null) { this.onLevelCb = cb; }

  setPrefs(prefs: VoicePrefs) {
    const prev = this.cfg.prefs;
    this.cfg.prefs = prefs;
    this.mic.setMuted(!prefs.mic_enabled);
    this.speaker.setMuted(!prefs.speak_replies);
    if (!this.sock?.open) return;
    if (prev.mic_enabled !== prefs.mic_enabled
      || prev.speak_replies !== prefs.speak_replies
      || prev.reply_pace !== prefs.reply_pace
      || prev.voice !== prefs.voice || prev.speed !== prefs.speed) {
      this.sock.sendJson({
        t: "prefs",
        patch: {
          mic_on: prefs.mic_enabled, speak_on: prefs.speak_replies,
          reply_pace: prefs.reply_pace, voice: prefs.voice, speed: prefs.speed,
        },
      });
    }
  }

  /**
   * Follow the host chat's thread and model WITHOUT rebuilding the client.
   *
   * These used to be constructor arguments behind a `useMemo`, so binding a
   * thread changed the dependency and produced a NEW client: voice on ->
   * server creates the chat -> the surface adopts it -> the hook rebuilds ->
   * the half-open session is torn down and a fresh one dials. That is a restart
   * loop triggered by the thing succeeding, and it is why `/voice/session` was
   * being posted over and over in the log.
   */
  setBinding(threadId: string | null, model?: string) {
    this.cfg.threadId = threadId;
    this.cfg.model = model;
  }

  // ---- lifecycle ---------------------------------------------------------
  /**
   * MUST be called from the voice-on click. The audio graph and the playback
   * element are created inside the gesture, once, and reused for the session:
   * on iOS anything created later cannot be started without another gesture.
   */
  async start(): Promise<void> {
    if (this.starting || this.sock) return;
    this.starting = true;
    this.patch({ state: "connecting", error: null, fatal: false, micFail: null });
    try {
      await this.speaker.start(24000, {
        onClauseStart: (token) => this.report(token, "start"),
        onClauseHeard: (token) => this.report(token, "end"),
      });
      this.patch({ echoCancelled: this.speaker.echoCancelled });

      const wantMic = this.cfg.prefs.input_mode !== "text";
      if (wantMic) {
        try {
          await this.mic.start({
            onFrame: (cursor, pcm) => this.sendFrame(cursor, pcm),
            onLevel: (v) => this.onLevelCb?.(v),
            onDeviceChange: (ok) => this.sock?.sendJson({ t: "device", ok }),
          });
          this.mic.setMuted(!this.cfg.prefs.mic_enabled);
        } catch (e) {
          // A mic failure is only fatal when the operator asked for a
          // voice-ONLY input lane. Otherwise voice mode continues with the
          // composer driving turns and replies still spoken, and the standing
          // reason sits in micFail. Bricking the chat over a mic is worse than
          // degrading to the lane that still works.
          const kind = e instanceof MicError ? e.kind : "unknown";
          this.patch({ micFail: kind, fatal: this.cfg.prefs.input_mode === "voice" });
          if (this.cfg.prefs.input_mode === "voice") {
            this.starting = false;
            this.speaker.stop();
            this.patch({ state: "idle" });
            return;
          }
        }
      }

      const sock = new VoiceSocket(streamUrl(), {
        onOpen: () => this.patch({ connected: true }),
        onClose: (retry, attempt) => this.patch({
          connected: false,
          state: retry ? "connecting" : "idle",
          // Say so rather than spinning. One blip is normal and recovers
          // silently; a run of them means something is actually wrong, and
          // "connecting" forever is the least useful thing to show.
          error: retry && attempt >= 3
            ? "Voice can't reach the server. Still trying."
            : this.snap.error,
        }),
        onJson: (msg) => this.onMessage(msg as ServerMessage),
        onAudio: (buf) => this.onAudio(buf),
      });
      sock.setHello(this.hello());
      this.sock = sock;
      sock.connect();
    } catch (e) {
      // ANYTHING THAT THROWS HERE USED TO LEAVE THE SPINNER TURNING FOREVER.
      //
      // `state` only leaves "connecting" when a snapshot arrives from the
      // server, so a failure before the socket is even dialed (a worklet that
      // will not load, an AudioContext the browser refuses) left the toggle
      // spinning with nothing said and nothing to retry. The caller does not
      // await this, so the rejection went nowhere. A failure has to be a
      // visible, recoverable state.
      this.speaker.stop();
      this.mic.stop();
      this.patch({
        state: "idle",
        error: `Voice could not start. ${String((e as Error)?.message || e)}`,
      });
    } finally {
      this.starting = false;
    }
  }

  stop() {
    this.sock?.sendJson({ t: "bye" });
    this.sock?.close();
    this.sock = null;
    this.mic.stop();
    this.speaker.stop();
    this.tokens.clear();
    this.snap = EMPTY;
    for (const fn of this.listeners) fn();
  }

  // ---- transport ---------------------------------------------------------
  private sendFrame(cursor: number, pcm: Int16Array) {
    this.sock?.sendAudio(encodeAudio(KIND_MIC, cursor, pcm));
  }

  private onAudio(buf: ArrayBuffer) {
    const { kind, cursor, pcm } = decodeAudio(buf);
    if (kind !== KIND_TTS) return;
    this.speaker.push(cursor, pcm);
  }

  /** The handshake, built FRESH each time it is needed.
   *
   *  It is rebuilt rather than stored because the socket replays the last one it
   *  was given on every reconnect, and the field that matters most changes after
   *  the first one is sent. A session that opened with no thread and then bound
   *  one kept re-announcing "no thread" on every rung of the backoff ladder, and
   *  the server answered each by creating another chat. That is the empty-"Voice"
   *  chat flood of 2026-08-12. Rebinding is now cheap and automatic: whoever
   *  learns the thread calls `bindThread`, which re-arms the socket. */
  private hello(): Record<string, unknown> {
    return {
      t: "hello",
      thread_id: this.cfg.threadId ?? undefined,
      context_key: this.cfg.contextKey,
      model: this.cfg.model,
      prefs: {
        voice: this.cfg.prefs.voice,
        speed: this.cfg.prefs.speed,
        reply_pace: this.cfg.prefs.reply_pace,
      },
    };
  }

  /** Adopt the thread this session is running in. Idempotent: the server names
   *  it on every state frame, not just once, so this is called constantly. */
  private bindThread(id: string | null | undefined) {
    if (!id || id === this.cfg.threadId) return;
    this.cfg.threadId = id;
    this.sock?.setHello(this.hello());
    this.cfg.onThreadBound?.(id);
  }

  private onMessage(msg: ServerMessage) {
    if (!msg || typeof msg !== "object") return;
    switch (msg.t) {
      case "ready":
        this.bindThread(msg.threadId);
        return;
      case "state":
        this.applyState(msg);
        return;
      case "clause":
        this.tokens.set(msg.token, { turn: msg.turn, index: msg.index });
        return;
      case "clause_end":
        this.speaker.finish(msg.token);
        return;
      case "flush":
        this.speaker.flush();
        return;
      case "mic_test":
        this.micTestResolve?.(msg.text);
        return;
    }
  }

  private applyState(s: ServerState) {
    // The thread can appear MID-SESSION now: a voice chat that started blank
    // gets one when the first utterance is generated, and this frame is how the
    // surface hears about it. Binding here is what keeps the spoken turn and the
    // typed transcript in one conversation instead of forking a second.
    this.bindThread(s.threadId);
    this.patch({
      state: s.phase,
      partial: s.partial,
      pendingUser: s.pendingUser,
      lastUser: s.lastUser,
      sentences: s.sentences,
      spokenCount: s.spokenCount,
      error: s.error,
      interruptRate: s.interruptRate,
      // The server is listening but the device says it stopped. Keep the
      // standing device reason visible without turning it into a turn error.
      previewing: !!s.partial && s.userSpeaking,
      tiers: s.tiers,
    });
  }

  /** Tell the server what actually made a sound. Only this side knows. */
  private report(token: number, phase: "start" | "end") {
    const at = this.tokens.get(token);
    if (!at) return;
    this.sock?.sendJson({ t: "played", turn: at.turn, index: at.index, phase });
    if (phase === "end") this.tokens.delete(token);
  }

  // ---- controls ----------------------------------------------------------
  /** Stop the reply now. An explicit press needs no heuristic. */
  interrupt() {
    this.speaker.flush();
    this.sock?.sendJson({ t: "interrupt" });
  }

  /** Scrap the turn AND the utterance that caused it (a misheard auto-send). */
  cancelTurn() {
    this.speaker.flush();
    this.sock?.sendJson({ t: "cancel" });
  }

  /** "I am done" without waiting for the turn-end model to agree. */
  forceEndUtterance() { this.sock?.sendJson({ t: "endpoint" }); }

  /**
   * Try the last utterance again after a failure.
   *
   * The server holds it, so this does not re-send a transcript the client may
   * have gone stale on. With nothing to retry it just clears the complaint: a
   * transient error also clears by itself the moment you speak again.
   */
  retry() {
    this.sock?.sendJson({ t: "retry" });
  }

  /** Typing holds the turn: the endpoint is deferred while the composer has text. */
  setTyping(active: boolean) { this.sock?.sendJson({ t: "typing", active }); }

  /** Send a typed message down the same path a spoken one takes. */
  submitText(text: string) {
    const t = text.trim();
    if (t) this.sock?.sendJson({ t: "text", body: t });
  }

  async resume(): Promise<void> {
    await this.speaker.resume();
    await this.mic.resume();
  }

  /**
   * Engine truth for the settings sheet, including WHICH perception tier is
   * live. Reported rather than configured: with the ONNX weights absent the
   * sheet must be able to say "energy VAD, heuristic turn detection" instead of
   * implying an accuracy the box does not have.
   */
  async probeCaps(): Promise<void> {
    try {
      const r = await fetch("/api/voice/health", { signal: AbortSignal.timeout(2500) });
      if (!r.ok) return;
      const h = await r.json();
      this.patch({
        tiers: {
          vad: h?.perception?.vad?.tier || "",
          turnEnd: h?.perception?.turn_end?.tier || "",
        },
        engines: {
          stt: !!h?.stt?.available,
          tts: !!h?.tts?.available,
          sttModel: h?.stt?.model || "",
          ttsVoice: h?.tts?.voice || "",
        },
      });
    } catch { /* the sheet falls back to "unknown"; a failed probe is not an error */ }
  }

  diagnostics() {
    return {
      micRunning: this.mic.running,
      deviceLabel: this.mic.deviceLabel,
      trackLive: this.mic.trackLive,
      secureContext: typeof window !== "undefined" ? window.isSecureContext : false,
      connected: this.snap.connected,
      echoCancelled: this.snap.echoCancelled,
      tiers: this.snap.tiers,
    };
  }

  /**
   * "Test microphone": ask the server to transcribe the last few seconds of the
   * real timeline. Every layer it touches is the one a real turn uses, which is
   * what makes it able to answer "is it me, the mic, the model or the network".
   */
  testMic(): Promise<{ ok: boolean; text?: string; reason?: string }> {
    if (!this.sock?.open) {
      return Promise.resolve({ ok: false, reason: "Not connected." });
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.micTestResolve = null;
        resolve({ ok: false, reason: "The speech engine did not answer." });
      }, 8000);
      this.micTestResolve = (text: string) => {
        clearTimeout(timer);
        this.micTestResolve = null;
        resolve({ ok: true, text });
      };
      this.sock!.sendJson({ t: "test_mic" });
    });
  }

  micTestResolve: ((text: string) => void) | null = null;
}
