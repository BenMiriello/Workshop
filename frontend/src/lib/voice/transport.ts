// One socket for the whole conversation: audio both ways plus control and state.
//
// Keeping them on one connection is what removes the ordering questions. The
// old design had three independent channels (a POST per STT segment, a POST per
// TTS sentence, an SSE stream per turn) that could each half-succeed, which is
// why the client needed an epoch counter to make stale continuations no-ops.
//
// Reconnect resumes rather than restarts: the mic cursor is absolute, so the
// server places frames by offset and a dropped connection costs the audio that
// was in flight and nothing else.

export type TransportEvents = {
  onJson: (msg: unknown) => void;
  onAudio: (buf: ArrayBuffer) => void;
  onOpen: () => void;
  // `attempt` counts CONSECUTIVE failures since the last successful open.
  // A surface that only knows 'it will retry' can only ever show a spinner,
  // which is what a persistent failure looked like: forever.
  onClose: (willRetry: boolean, attempt: number) => void;
};

const MAX_BACKOFF_MS = 8000;
// Above this the socket is behind the operator rather than carrying them, and
// sending more only adds latency to what is already queued. Dropping the oldest
// frames is the right loss: they are the ones furthest in the past.
const MAX_BUFFERED_BYTES = 512 * 1024;

export class VoiceSocket {
  private ws: WebSocket | null = null;
  private events: TransportEvents;
  private url: string;
  private closedByUs = false;
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private hello: Record<string, unknown> | null = null;

  constructor(url: string, events: TransportEvents) {
    this.url = url;
    this.events = events;
  }

  get open(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /** The handshake, replayed automatically on every reconnect. */
  setHello(hello: Record<string, unknown>) {
    this.hello = hello;
  }

  connect() {
    this.closedByUs = false;
    this.dial();
  }

  private dial() {
    if (this.ws) { try { this.ws.close(); } catch { /* already closing */ } }
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      if (this.hello) ws.send(JSON.stringify(this.hello));
      this.events.onOpen();
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        try { this.events.onJson(JSON.parse(e.data)); } catch { /* not ours */ }
      } else {
        this.events.onAudio(e.data as ArrayBuffer);
      }
    };
    ws.onclose = () => {
      const willRetry = !this.closedByUs;
      this.events.onClose(willRetry, this.retry + 1);
      if (!willRetry) return;
      const wait = Math.min(MAX_BACKOFF_MS, 250 * 2 ** this.retry++);
      this.timer = setTimeout(() => this.dial(), wait);
    };
    ws.onerror = () => { /* onclose carries the outcome */ };
  }

  sendJson(payload: unknown) {
    if (!this.open) return;
    this.ws!.send(JSON.stringify(payload));
  }

  sendAudio(buf: ArrayBuffer) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > MAX_BUFFERED_BYTES) return;
    ws.send(buf);
  }

  close() {
    this.closedByUs = true;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    try { this.ws?.close(); } catch { /* already closing */ }
    this.ws = null;
  }
}

export function streamUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/voice/stream`;
}
