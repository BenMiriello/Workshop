// Speech playback: a jitter buffer that plays queued PCM and reports what was
// actually heard.
//
// It replaces one `new Audio(blobUrl)` per sentence. That design had three
// faults that all showed up on the phone: on iOS a programmatic play() on a
// FRESH element outside a user gesture is blocked, so playback died after the
// first turn; every sentence leaked an object URL and a graph node; and
// stopping meant pausing an element, which is not the same as stopping, because
// the audio already fetched still had to be thrown away.
//
// Here there is ONE node for the whole session, created inside the voice-on
// gesture. Interrupting is emptying a queue.
//
// It reports `start` when a clause first makes a sound and `drained` when its
// last sample has gone out. Those reports are what the server treats as the
// truth about what the operator heard, because only this side knows when a
// sample actually left the speaker, and the difference between a continuation
// and an interruption turns on exactly that.

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const src = (options.processorOptions && options.processorOptions.sourceRate) || 24000;
    this.ratio = src / sampleRate;
    this.queue = [];      // [{ token, pcm: Int16Array }], many chunks per token
    this.pos = 0;         // fractional read index into queue[0].pcm
    this.current = null;  // token currently sounding
    this.level = 0;
    this.port.onmessage = (e) => {
      const d = e.data;
      if (!d) return;
      if (d.type === "push") {
        this.queue.push({ token: d.token, pcm: d.pcm });
      } else if (d.type === "flush") {
        // Barge-in. Everything queued goes, including the clause in flight:
        // stopping means stopping, not "after this sentence".
        const was = this.current;
        this.queue.length = 0;
        this.pos = 0;
        this.current = null;
        if (was !== null) this.port.postMessage({ type: "drained", token: was, cut: true });
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    let peak = 0;

    for (let i = 0; i < out.length; i++) {
      const head = this.queue[0];
      if (!head) {
        // Underrun, or nothing to say. Silence, and if a clause was sounding it
        // has now finished going out.
        if (this.current !== null) {
          this.port.postMessage({ type: "drained", token: this.current });
          this.current = null;
        }
        out[i] = 0;
        continue;
      }
      if (this.current !== head.token) {
        if (this.current !== null) {
          this.port.postMessage({ type: "drained", token: this.current });
        }
        this.current = head.token;
        this.port.postMessage({ type: "start", token: head.token });
      }
      const i0 = Math.floor(this.pos);
      if (i0 >= head.pcm.length) {
        // This chunk is spent. Carry the fractional remainder into the next one
        // so the seam between two chunks of the same clause does not click.
        this.queue.shift();
        this.pos -= head.pcm.length;
        if (this.pos < 0) this.pos = 0;
        i -= 1;
        continue;
      }
      const f = this.pos - i0;
      const a = head.pcm[i0];
      // The next sample may live in the following chunk; using `a` twice at a
      // boundary is a sub-sample error nobody can hear, and reaching across
      // chunks here is not worth the bookkeeping.
      const b = i0 + 1 < head.pcm.length ? head.pcm[i0 + 1] : a;
      const s = (a * (1 - f) + b * f) / 32768;
      out[i] = s;
      const abs = s < 0 ? -s : s;
      if (abs > peak) peak = abs;
      this.pos += this.ratio;
    }

    // A cheap envelope for the orb. Purely cosmetic: nothing decides anything
    // from a level any more, which is most of what this redesign is about.
    this.level = this.level * 0.85 + peak * 0.15;
    return true;
  }
}

registerProcessor("voice-playback", PlaybackProcessor);
