// The microphone, as a device. It captures and it reports; it decides nothing.
//
// One `getUserMedia` for the whole session, one AudioWorklet, one continuous
// stream of 20 ms PCM frames with an absolute sample cursor. The device is
// never stopped and restarted to cut audio, because cutting happens on the
// server by sample offset, which is the change the whole redesign rests on.
//
// Muting stops SENDING, it does not close the device: reopening a device can
// fail, and a mute that can fail is a mute the operator cannot trust. It also
// means unmuting is instant.

import { signalVerdict } from "../voiceSignal";
import { MicError, classifyGumError, micPreflight, type MicFailKind } from "./errors";

export const MIC_RATE = 16000;

export type CaptureEvents = {
  onFrame: (cursor: number, pcm: Int16Array) => void;
  // Display only. Nothing decides anything from a level any more: speech
  // detection is a trained model on the server, and device health is read from
  // the track. This exists so the orb can breathe.
  onLevel?: (v: number) => void;
  // The track itself says it stopped: ended (unplugged, permission revoked) or
  // muted (another app took it). Silence is NEVER evidence of this, which is
  // the rule that produced "No signal from the microphone" over a quiet room.
  onDeviceChange?: (ok: boolean) => void;
};

export class MicDevice {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private events: CaptureEvents | null = null;
  private cursor = 0;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private lastOk = true;
  fail: MicFailKind | null = null;
  running = false;

  get trackLive(): boolean {
    const t = this.stream?.getAudioTracks()[0];
    return !!t && t.readyState === "live" && !t.muted;
  }

  get deviceLabel(): string | null {
    return this.stream?.getAudioTracks()[0]?.label || null;
  }

  /** The mic stream, for the echo canceller to reference. */
  get mediaStream(): MediaStream | null {
    return this.stream;
  }

  async start(events: CaptureEvents): Promise<void> {
    if (this.running) { this.events = events; return; }
    this.events = events;
    const pre = micPreflight();
    if (pre) { this.fail = pre.kind; throw pre; }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Ask for the browser's own echo canceller. It is far better than
          // anything we could write, PROVIDED it can see the playback as a
          // reference; see playback.ts for how that is arranged.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
    } catch (e) {
      const err = classifyGumError(e);
      this.fail = err.kind;
      throw err;
    }
    // Ask for 16k so no resampling is needed. Safari usually ignores this and
    // gives 48k; the worklet resamples either way, so nothing downstream cares.
    const Ctor = window.AudioContext
      || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new Ctor({ sampleRate: MIC_RATE });
    try {
      await this.ctx.audioWorklet.addModule("/voice-capture-worklet.js");
    } catch (e) {
      this.fail = "unsupported";
      throw new MicError("unsupported", `audio worklet unavailable (${String(e)})`);
    }
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "voice-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      processorOptions: { targetRate: MIC_RATE },
    });
    this.node.port.onmessage = (e: MessageEvent) => {
      const pcm = e.data as Int16Array;
      this.events?.onFrame(this.cursor, pcm);
      this.cursor = (this.cursor + pcm.length) >>> 0;
      if (this.events?.onLevel) this.events.onLevel(rms(pcm));
    };
    this.source.connect(this.node);
    this.fail = null;
    this.running = true;
    this.lastOk = true;
    // Poll the TRACK, not the level. This is the whole of device health.
    this.watchdog = setInterval(() => this.checkDevice(), 1500);
  }

  private checkDevice() {
    if (!this.running) return;
    const ok = signalVerdict({
      trackLive: this.trackLive,
      // The remaining inputs belonged to a level-based guess that no longer
      // exists: speech detection moved to the server, so nothing here can or
      // should infer health from loudness. Passing zeroes keeps the tested rule
      // (voiceSignal.test.ts) as the single definition of "device lost".
      calibrated: false,
      heardSpeechEver: false,
      peak: 0,
      floor: 0,
      threshold: 0,
    }) !== "device-lost";
    if (ok !== this.lastOk) {
      this.lastOk = ok;
      this.events?.onDeviceChange?.(ok);
    }
  }

  /** Stop sending without closing the device. */
  setMuted(on: boolean) {
    this.node?.port.postMessage({ type: "mute", on });
  }

  async resume(): Promise<void> {
    if (this.ctx?.state === "suspended") await this.ctx.resume().catch(() => {});
  }

  stop() {
    this.running = false;
    if (this.watchdog) { clearInterval(this.watchdog); this.watchdog = null; }
    try { this.node?.port.close(); } catch { /* already gone */ }
    this.node?.disconnect();
    this.source?.disconnect();
    this.ctx?.close().catch(() => {});
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.source = null;
  }
}

function rms(pcm: Int16Array): number {
  let s = 0;
  for (let i = 0; i < pcm.length; i++) s += pcm[i] * pcm[i];
  return Math.min(1, Math.sqrt(s / pcm.length) / 8000);
}
