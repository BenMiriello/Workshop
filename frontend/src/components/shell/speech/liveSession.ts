// One live dictation: the microphone, the socket to /speech/live, and the audio
// kept until the server has turned it into text.
//
// The socket reconnects on its own (VoiceSocket). Each time it opens it says
// where its audio starts and is sent everything not yet settled, so a dropped
// connection delays words and loses none. If it never comes back, Stop sends
// the kept audio to the transcribe endpoint in one go, so even a dictation
// that never went live still ends in text. The server is kept told what can
// be said on the pages shown (spokenWire.ts).

import { MicDevice, MIC_RATE } from "../../../lib/voice/capture";
import { encodeAudio, KIND_MIC } from "../../../lib/voice/protocol";
import { VoiceSocket } from "../../../lib/voice/transport";
import { speechApi } from "../../../lib/speech/api";
import {
  liveUrl, parseLiveMessage, PendingAudio, type LiveActions, type LiveCommand, type LiveFinal,
} from "../../../lib/speech/liveWire";
import { encodeWav } from "../../../lib/speech/wav";
import { watchSpoken } from "./spokenWire";

export type LiveEvents = {
  /** The open stretch as heard now (services/speech/live.py). */
  onText: (text: string, from: number, to: number) => void;
  onFinal: (m: LiveFinal) => void;
  /** A spoken command, its words cut from the stream. */
  onCommand: (m: LiveCommand) => void;
  /** A spoken pause or resume changed whether words are written. */
  onPaused: (paused: boolean) => void;
  /** Words said that were written nowhere and were no command. */
  onUnwritten: (text: string) => void;
  /** A newer dictation of the same user started elsewhere. */
  onTaken: () => void;
  /** The command mode changed; `said`: by a spoken switch. */
  onMode: (mode: string, said: boolean) => void;
  onLevel: (v: number) => void;
  /** Connected to the server and hearing, or not (it keeps trying). */
  onConnected: (ok: boolean) => void;
  onMicLost: () => void;
  onError: (message: string) => void;
};

// Thirty minutes of audio, kept while the server is out of reach.
const MAX_PENDING = 30 * 60 * MIC_RATE;
// How long Stop waits for the server to settle the last words.
const FINISH_MS = 20_000;
// How long Stop waits for a lost connection to come back before transcribing
// what it kept in one request instead.
const RECONNECT_MS = 6_000;

export class LiveSession {
  private mic = new MicDevice();
  private socket: VoiceSocket;
  private pending = new PendingAudio(MAX_PENDING);
  private done: (() => void) | null = null;
  private stopping = false;
  private closed = false;
  // Stop stopped waiting for the server and transcribed the kept audio itself;
  // anything the server says after that would be said twice.
  private gaveUp = false;
  // The server answered at least once; until then there is nothing to wait for.
  private reached = false;
  private settledTo = 0;
  private paused = false;
  private mode: string | null = null;
  private numbers = false;
  private actions: LiveActions | null = null;
  private unwatch: (() => void) | null = null;

  /** `id` names this dictation to the server, which keeps its audio under it
   *  for the clipboard. */
  constructor(private id: string, private events: LiveEvents) {
    this.socket = new VoiceSocket(liveUrl(), {
      onOpen: () => this.resend(),
      onClose: () => {
        this.events.onConnected(false);
        this.socket.setHello(this.hello(this.pending.from));
      },
      onAudio: () => {},
      onJson: (raw) => this.receive(raw),
    });
  }

  /** Open the microphone and the socket. Throws a MicError the caller shows. */
  async start(): Promise<void> {
    this.socket.setHello(this.hello(0));
    this.socket.connect();
    this.unwatch = watchSpoken((m) => {
      this.actions = m;
      if (this.socket.open) this.socket.sendJson({ t: "actions", ...m });
    });
    try {
      await this.mic.start({
        onFrame: (cursor, pcm) => {
          if (this.closed || this.stopping) return;
          this.pending.push(cursor, pcm);
          this.socket.sendAudio(encodeAudio(KIND_MIC, cursor, pcm));
        },
        onLevel: (v) => this.events.onLevel(v),
        onDeviceChange: (ok) => { if (!ok) this.events.onMicLost(); },
      });
      await this.mic.resume();
    } catch (e) {
      this.cancel();
      throw e;
    }
  }

  private hello(from: number) {
    return { t: "hello", from, dictation: this.id, paused: this.paused, ...(this.mode ? { mode: this.mode } : {}) };
  }

  private resend(): void {
    if (this.numbers) this.socket.sendJson({ t: "numbers", on: true });
    if (this.actions) this.socket.sendJson({ t: "actions", ...this.actions });
    for (const f of this.pending.all()) this.socket.sendAudio(encodeAudio(KIND_MIC, f.cursor, f.pcm));
    if (this.stopping) this.socket.sendJson({ t: "stop" });
  }

  private receive(raw: unknown): void {
    const m = parseLiveMessage(raw);
    if (!m || this.closed || this.gaveUp) return;
    if (m.t === "ready") {
      this.reached = true;
      this.events.onConnected(true);
    }
    else if (m.t === "text") this.events.onText(m.text, m.from, m.to);
    else if (m.t === "final") {
      this.settle(m.to);
      this.events.onFinal(m);
    } else if (m.t === "command") {
      this.settle(m.to);
      this.events.onCommand(m);
    } else if (m.t === "paused") {
      this.paused = m.paused;
      this.events.onPaused(m.paused);
    } else if (m.t === "mode") {
      this.mode = m.mode;
      this.events.onMode(m.mode, m.said);
    } else if (m.t === "unwritten") this.events.onUnwritten(m.text);
    else if (m.t === "taken") this.events.onTaken();
    else if (m.t === "done") this.done?.();
    else if (m.t === "error") this.events.onError(m.message);
  }

  private settle(to: number): void {
    this.settledTo = Math.max(this.settledTo, to);
    this.pending.settle(to);
  }

  /** The words shown through sample `to` are final; the server starts the
   *  next stretch after them. Sent again on reconnect as where audio starts. */
  commit(to: number): void {
    this.settle(to);
    if (this.socket.open) this.socket.sendJson({ t: "commit", to });
  }

  /** Stop or start writing words, keeping the microphone open so a spoken
   *  resume is still heard. */
  pause(paused: boolean): void {
    this.paused = paused;
    if (this.socket.open) this.socket.sendJson({ t: "pause", paused });
  }

  /** Change the command mode (services/speech/modes.py). */
  setMode(mode: string): void {
    this.mode = mode;
    if (this.socket.open) this.socket.sendJson({ t: "mode", mode });
  }

  /** Numbered things are on screen: a number said on its own picks one. */
  setNumbers(on: boolean): void {
    if (on === this.numbers) return;
    this.numbers = on;
    if (this.socket.open) this.socket.sendJson({ t: "numbers", on });
  }

  /** Stop listening and settle every word said. Resolves when the last text
   *  has been delivered through onFinal. */
  async stop(): Promise<void> {
    if (this.stopping || this.closed) return;
    this.stopping = true;
    this.mic.stop();
    const finished = new Promise<boolean>((resolve) => {
      if (!this.reached) { resolve(false); return; }
      this.done = () => resolve(true);
      setTimeout(() => resolve(false), this.socket.open ? FINISH_MS : RECONNECT_MS);
    });
    if (this.socket.open) this.socket.sendJson({ t: "stop" });
    const ok = await finished;
    if (!ok) {
      this.gaveUp = true;
      if (this.pending.samples > 0) await this.transcribeKept();
    }
    this.close();
  }

  private async transcribeKept(): Promise<void> {
    const frames = this.pending.all();
    const from = this.settledTo;
    const last = frames[frames.length - 1];
    const to = last.cursor + last.pcm.length;
    this.pending.clear();
    try {
      const text = await speechApi.transcribe(encodeWav(frames.map((f) => f.pcm), MIC_RATE));
      this.events.onFinal({ text, from, to, rest: "", restTo: to });
    } catch {
      this.events.onError("The last words could not be turned into text.");
    }
  }

  /** Throw away what was not settled yet and close everything. */
  cancel(): void {
    this.pending.clear();
    this.close();
  }

  private close(): void {
    this.closed = true;
    this.unwatch?.();
    this.unwatch = null;
    this.mic.stop();
    this.socket.close();
    this.done = null;
  }
}
