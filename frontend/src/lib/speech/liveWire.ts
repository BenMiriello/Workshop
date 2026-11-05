// Live dictation's socket, as data: what the server says, and the audio the
// client must keep until the server has settled it.
//
// The server sends the words of the open stretch whole as they are revised
// (`text`), and settles part of it now and then (`final`), saying how far into
// the audio the settled part reaches (`to`, a sample offset). The client says
// the same when it lets go of words itself (a commit). A spoken command said on
// its own arrives as `command`, its words already cut from the stream, and the
// server says when a spoken pause or resume changed whether it is writing
// (`paused`), words said while nothing is written that were no command
// (`unwritten`, kept so nothing said is lost), that a newer dictation of the
// same user started elsewhere (`taken`: this one stops), and when the command mode changed (`mode`; `said` when by a
// spoken switch). Words said to the app that are none of its phrases arrive as
// the command "guess", with what they most likely mean (`guesses`, best first,
// and how likely it is they are no command at all, `none`), or `loading` while
// the server's model for that is not ready. The client says which actions its
// pages declare and which commands do not apply now (`actions`); a declared
// one heard comes back as a command named by its id. Everything after the settled point is held here,
// so a dropped connection resends exactly the audio that was never turned into
// text, and a server that cannot be reached at all still leaves the whole
// recording on the device to be transcribed in one go when dictation stops.

export type LiveMessage =
  | { t: "ready"; vad: string }
  | { t: "text"; text: string; from: number; to: number }
  | { t: "final"; text: string; from: number; to: number; rest: string; restTo: number }
  | { t: "command"; name: string; arg: string | null; words: string; from: number; to: number;
      guesses?: Guess[]; none?: number; loading?: boolean }
  | { t: "paused"; paused: boolean }
  | { t: "mode"; mode: string; said: boolean }
  | { t: "unwritten"; text: string }
  | { t: "taken" }
  | { t: "done" }
  | { t: "error"; message: string };

export type Guess = { name: string; p: number; text?: string };

/** An action a page declares (components/shell/speech/appActions.ts), as
 *  the client tells the server about it. `phrases` are heard alone, or with
 *  the argument after them when `takesArg`; each of `fixed` is a wording that
 *  means one choice. Heard, it comes back as a command named by its `id`. */
export type LiveAction = {
  id: string; title: string; description: string; phrases: string[]; fixed: [string, string][];
  examples: string[]; takesArg: boolean;
};

/** `{"t": "actions", ...}`: every action declared on the open pages, and the
 *  ids of commands and actions that do not apply now, whose phrases are then
 *  heard as words. */
export type LiveActions = { list: LiveAction[]; off: string[] };

export type LiveFinal = Omit<Extract<LiveMessage, { t: "final" }>, "t">;
export type LiveCommand = Omit<Extract<LiveMessage, { t: "command" }>, "t">;

export function parseLiveMessage(raw: unknown): LiveMessage | null {
  if (!raw || typeof raw !== "object") return null;
  const m = raw as Record<string, unknown>;
  const text = typeof m.text === "string" ? m.text : "";
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const from = num(m.from);
  const to = num(m.to);
  switch (m.t) {
    case "ready": return { t: "ready", vad: String(m.vad ?? "") };
    case "text": return from !== null && to !== null ? { t: "text", text, from, to } : null;
    case "final": {
      if (from === null || to === null) return null;
      const rest = typeof m.rest === "string" ? m.rest : "";
      return { t: "final", text, from, to, rest, restTo: num(m.restTo) ?? to };
    }
    case "command": {
      if (from === null || to === null || typeof m.name !== "string" || !m.name) return null;
      const arg = typeof m.arg === "string" && m.arg.trim() ? m.arg.trim() : null;
      const words = typeof m.words === "string" ? m.words : "";
      if (m.name !== "guess") return { t: "command", name: m.name, arg, words, from, to };
      const guesses = (Array.isArray(m.guesses) ? m.guesses : []).flatMap((g): Guess[] => (
        g && typeof g.name === "string" && typeof g.p === "number"
          ? [{ name: g.name, p: g.p, ...(typeof g.text === "string" ? { text: g.text } : {}) }] : []));
      return { t: "command", name: "guess", arg, words, from, to, guesses, none: num(m.none) ?? 1, loading: m.loading === true };
    }
    case "paused": return { t: "paused", paused: m.paused === true };
    case "mode": return typeof m.mode === "string" && m.mode ? { t: "mode", mode: m.mode, said: m.said === true } : null;
    case "unwritten": return text ? { t: "unwritten", text } : null;
    case "taken": return { t: "taken" };
    case "done": return { t: "done" };
    case "error": return { t: "error", message: String(m.message ?? "") };
    default: return null;
  }
}

export function liveUrl(loc: { protocol: string; host: string } = location): string {
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}/api/speech/live`;
}

type Frame = { cursor: number; pcm: Int16Array };

/** The audio not yet settled into text, in order. Bounded: past `maxSamples`
 *  the oldest frames go, which only happens when the server has been out of
 *  reach for that long. */
export class PendingAudio {
  private frames: Frame[] = [];
  private held = 0;
  private settled = 0;

  constructor(private maxSamples: number) {}

  push(cursor: number, pcm: Int16Array): void {
    if (cursor + pcm.length <= this.settled) return;
    this.frames.push({ cursor, pcm });
    this.held += pcm.length;
    while (this.held > this.maxSamples && this.frames.length > 1) {
      const gone = this.frames.shift()!;
      this.held -= gone.pcm.length;
    }
  }

  /** The server has settled everything before `to`. */
  settle(to: number): void {
    this.settled = Math.max(this.settled, to);
    while (this.frames.length && this.frames[0].cursor + this.frames[0].pcm.length <= this.settled) {
      this.held -= this.frames.shift()!.pcm.length;
    }
  }

  /** Where a new connection's audio starts. */
  get from(): number {
    return this.frames.length ? Math.max(this.settled, this.frames[0].cursor) : this.settled;
  }

  get samples(): number {
    return this.held;
  }

  all(): Frame[] {
    return this.frames.slice();
  }

  clear(): void {
    this.frames = [];
    this.held = 0;
  }
}
