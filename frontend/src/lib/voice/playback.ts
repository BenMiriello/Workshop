// Speech output, and the echo canceller that makes talking over it safe.
//
// ============================ THE ECHO PROBLEM ============================
// The old code computed `net = rms - outputProbe() * echoK` and required the
// result to clear an elevated bar for 180ms before counting as an interruption.
// That cannot work, and not because the constants were wrong: an RMS envelope
// has no delay model. The microphone hears the speaker 20 to 150ms after
// `outputLevel()` reports it, so a time-misaligned envelope was being
// subtracted from the signal. It removed energy where there was no echo yet,
// swallowing the operator's first syllable, and failed to remove it where there
// was, leaking the assistant's voice into the detector.
//
// Worse, an entire subsystem existed to compensate for the resulting dirty
// microphone: `closeCleanWindow`, `spokeWhileThinking`, discard-on-completion,
// the barge sustain window. That subsystem is also what produced the worst
// regression of the last round, where the assistant's own reply was transcribed
// and sent as the operator's next message.
//
// ============================ THE FIX =====================================
// The browser already ships a real echo canceller (WebRTC AEC3) and
// getUserMedia asks for it. AEC3 can only cancel audio it holds as a PLAYOUT
// REFERENCE, and audio scheduled through Web Audio is not one. Routing playback
// through a local RTCPeerConnection pair makes the browser classify it as
// remote-participant audio, which IS a reference, so AEC3 cancels it properly.
//
// It costs one local peer connection (no network, no ICE servers) and a few ms.
// If it fails to establish for any reason we connect straight to the
// destination and report `echoCancelled = false`, so the surface can say the
// session is half-duplex instead of silently hallucinating.

const LOOPBACK_TIMEOUT_MS = 2500;

// The shared output context. Created by primeAudio() from the voice-on CLICK and
// reused for the whole session.
//
// This has to happen in the gesture. React effects are not gestures, so a
// context created in the effect that starts the session is born suspended on
// iOS and every later play() is refused. The old code knew this (`unlockAudio`
// was called from the toggle) and then undid it by creating a NEW Audio element
// per sentence, each of which needed its own gesture it was never going to get.
let shared: AudioContext | null = null;

function AudioCtor(): typeof AudioContext {
  return window.AudioContext
    || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
}

/** Call SYNCHRONOUSLY from the voice-on click. Idempotent. */
export function primeAudio(rate = 24000): AudioContext {
  if (!shared) shared = new (AudioCtor())({ sampleRate: rate });
  if (shared.state === "suspended") void shared.resume().catch(() => {});
  try {
    // A one-sample silent buffer, started from the gesture, is what marks the
    // context as user-activated for the rest of its life.
    const buf = shared.createBuffer(1, 1, shared.sampleRate);
    const src = shared.createBufferSource();
    src.buffer = buf;
    src.connect(shared.destination);
    src.start(0);
  } catch { /* best effort: the resume above is the part that matters */ }
  return shared;
}

export type PlaybackEvents = {
  onClauseStart: (token: number) => void;
  onClauseHeard: (token: number) => void;
};

export class SpeakerDevice {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private gain: GainNode | null = null;
  private dest: MediaStreamAudioDestinationNode | null = null;
  private pc1: RTCPeerConnection | null = null;
  private pc2: RTCPeerConnection | null = null;
  private el: HTMLAudioElement | null = null;
  private events: PlaybackEvents | null = null;
  /** Tokens the server has finished sending audio for. */
  private complete = new Set<number>();
  /** Tokens the worklet has finished playing but the server has not closed. */
  private drained = new Set<number>();
  private started = new Set<number>();
  echoCancelled = false;
  running = false;

  /**
   * MUST be called synchronously inside the voice-on click.
   *
   * Everything that the autoplay policy can block is created here, once, and
   * reused for the whole session. The old design created a fresh `Audio`
   * element per sentence, and on iOS a programmatic play() on a fresh element
   * outside a gesture is refused, which is why mobile playback died after the
   * first turn.
   */
  async start(sourceRate: number, events: PlaybackEvents): Promise<void> {
    if (this.running) { this.events = events; return; }
    this.events = events;
    this.ctx = primeAudio(sourceRate);
    if (this.ctx.state === "suspended") await this.ctx.resume().catch(() => {});
    await this.ctx.audioWorklet.addModule("/voice-playback-worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "voice-playback", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { sourceRate },
    });
    this.node.port.onmessage = (e: MessageEvent) => this.onWorklet(e.data);
    this.gain = this.ctx.createGain();
    this.node.connect(this.gain);
    this.echoCancelled = await this.wireLoopback();
    if (!this.echoCancelled) this.gain.connect(this.ctx.destination);
    this.running = true;
  }

  /** Route playback through a local peer connection so AEC3 can see it. */
  private async wireLoopback(): Promise<boolean> {
    if (typeof RTCPeerConnection === "undefined" || !this.ctx || !this.gain) return false;
    try {
      this.dest = this.ctx.createMediaStreamDestination();
      this.gain.connect(this.dest);
      const pc1 = new RTCPeerConnection();
      const pc2 = new RTCPeerConnection();
      this.pc1 = pc1;
      this.pc2 = pc2;
      pc1.onicecandidate = (e) => { if (e.candidate) void pc2.addIceCandidate(e.candidate); };
      pc2.onicecandidate = (e) => { if (e.candidate) void pc1.addIceCandidate(e.candidate); };
      const el = document.createElement("audio");
      el.autoplay = true;
      // Not in the document: it never needs to be, and appending it would put a
      // stray element in the tree for the whole session.
      this.el = el;
      const connected = new Promise<boolean>((resolve) => {
        pc2.ontrack = (e) => {
          el.srcObject = e.streams[0];
          el.play().then(() => resolve(true)).catch(() => resolve(false));
        };
        setTimeout(() => resolve(false), LOOPBACK_TIMEOUT_MS);
      });
      // addTrack, not the deprecated addStream: with addStream the receiving
      // side does not always fire ontrack.
      const track = this.dest.stream.getAudioTracks()[0];
      pc1.addTrack(track, this.dest.stream);
      const offer = await pc1.createOffer();
      await pc1.setLocalDescription(offer);
      await pc2.setRemoteDescription(offer);
      const answer = await pc2.createAnswer();
      await pc2.setLocalDescription(answer);
      await pc1.setRemoteDescription(answer);
      const ok = await connected;
      if (!ok) this.tearDownLoopback();
      return ok;
    } catch {
      this.tearDownLoopback();
      return false;
    }
  }

  private tearDownLoopback() {
    try { this.pc1?.close(); } catch { /* already closed */ }
    try { this.pc2?.close(); } catch { /* already closed */ }
    if (this.el) { this.el.srcObject = null; this.el = null; }
    try { this.dest?.disconnect(); } catch { /* not connected */ }
    this.pc1 = null;
    this.pc2 = null;
    this.dest = null;
  }

  private onWorklet(d: { type: string; token: number }) {
    if (!d) return;
    if (d.type === "start") {
      if (this.started.has(d.token)) return;
      this.started.add(d.token);
      this.events?.onClauseStart(d.token);
      return;
    }
    if (d.type === "drained") {
      // A clause arrives as many frames, so "the queue emptied" only means it
      // was fully heard once the server has also said it sent everything.
      // Without that reconciliation an underrun mid-clause would report the
      // clause heard, and `heard` is what decides continuation vs interruption.
      if (this.complete.has(d.token)) {
        this.complete.delete(d.token);
        this.events?.onClauseHeard(d.token);
      } else {
        this.drained.add(d.token);
      }
    }
  }

  /** The server has sent every frame for this clause. */
  finish(token: number) {
    if (this.drained.has(token)) {
      this.drained.delete(token);
      this.events?.onClauseHeard(token);
    } else {
      this.complete.add(token);
    }
  }

  push(token: number, pcm: Int16Array) {
    this.node?.port.postMessage({ type: "push", token, pcm }, [pcm.buffer]);
  }

  /** Barge-in: drop everything queued, now. */
  flush() {
    this.node?.port.postMessage({ type: "flush" });
    this.complete.clear();
    this.drained.clear();
    this.started.clear();
  }

  setMuted(on: boolean) {
    if (this.gain) this.gain.gain.value = on ? 0 : 1;
    if (on) this.flush();
  }

  async resume(): Promise<void> {
    if (this.ctx?.state === "suspended") await this.ctx.resume().catch(() => {});
    if (this.el && this.el.paused) await this.el.play().catch(() => {});
  }

  stop() {
    this.running = false;
    this.flush();
    this.tearDownLoopback();
    try { this.node?.port.close(); } catch { /* already gone */ }
    this.node?.disconnect();
    this.gain?.disconnect();
    // The context is SHARED and user-activated. Closing it would mean the next
    // voice session needs another gesture to make any sound, which is exactly
    // the iOS failure this design removes.
    this.ctx = null;
    this.node = null;
    this.gain = null;
  }
}
