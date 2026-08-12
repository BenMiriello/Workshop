// Microphone tap: resample to 16 kHz mono and emit fixed 20 ms PCM frames.
//
// This runs on the audio thread, which is the point. The old capture path used
// MediaRecorder on the main thread and cut audio into files by stopping and
// restarting the device, which meant a real deaf gap at every cut, a container
// per segment, and a different format on Safari. None of that exists here: the
// stream is continuous and the server addresses it by sample offset.
//
// A busy main thread (React rendering a long transcript, say) cannot drop
// frames from here, because it is not involved.

const FRAME = 320; // 20 ms at 16 kHz

class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    // `sampleRate` is a global in the AudioWorklet scope: the context's real
    // rate, which is NOT necessarily the one we asked for. Chrome usually
    // honours 16000; Safari commonly gives 48000 regardless. Resampling here
    // means nothing downstream ever has to know which happened.
    this.ratio = sampleRate / target;
    this.pos = 0;
    this.carry = new Float32Array(0);
    this.frame = new Int16Array(FRAME);
    this.n = 0;
    this.muted = false;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === "mute") this.muted = !!e.data.on;
    };
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (this.muted) return true;

    // Carry the tail of the previous block so interpolation is continuous
    // across block boundaries. Without it every 128-sample block would start
    // from a discontinuity, which is audible to an ASR model as a click.
    const src = new Float32Array(this.carry.length + ch.length);
    src.set(this.carry, 0);
    src.set(ch, this.carry.length);

    let p = this.pos;
    while (Math.floor(p) + 1 < src.length) {
      const i = Math.floor(p);
      const f = p - i;
      const s = src[i] * (1 - f) + src[i + 1] * f;
      // Clamp before the int16 cast, so a hot input wraps to full scale rather
      // than round-tripping to the opposite sign as a loud crackle.
      const c = s > 1 ? 1 : s < -1 ? -1 : s;
      this.frame[this.n++] = (c * 32767) | 0;
      if (this.n === FRAME) {
        const out = this.frame;
        this.frame = new Int16Array(FRAME);
        this.n = 0;
        this.port.postMessage(out, [out.buffer]);
      }
      p += this.ratio;
    }
    const consumed = Math.floor(p);
    this.carry = src.slice(consumed);
    this.pos = p - consumed;
    return true;
  }
}

registerProcessor("voice-capture", CaptureProcessor);
