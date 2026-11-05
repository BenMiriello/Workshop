// EVERY PLAYER AND AUDIO CONTEXT A FRAME MAKES, FOUND FOR A HOLD.
//
// A hold (./frameMedia.ts) finds `<video>` and `<audio>` in a document, but a
// script can play sound with no element in the page at all: `new Audio()`,
// or a Web Audio graph. Each same-origin document has its window's `play`,
// `AudioContext` and source `start` wrapped so whatever plays is remembered,
// and while the document is held a player that starts is paused and a
// context that starts is suspended. Releasing restarts nothing, the same as
// for elements: a context the hold suspended runs again when the app next
// starts a sound in it.

type Win = Window & typeof globalThis;

export interface FrameAudio {
  media: Set<HTMLMediaElement>;
  contexts: Set<BaseAudioContext>;
  /** Contexts the hold suspended, which the app's next sound wakes. */
  quieted: Set<BaseAudioContext>;
  held: number;
}

const registries = new WeakMap<Window, FrameAudio>();

export function frameAudioOf(win: Window): FrameAudio | undefined {
  return registries.get(win);
}

function wrapPlay(win: Win, reg: FrameAudio) {
  const proto = win.HTMLMediaElement?.prototype;
  if (!proto) return;
  const play = proto.play;
  const onPlay = (e: Event) => { if (reg.held > 0) (e.target as HTMLMediaElement).pause(); };
  proto.play = function (this: HTMLMediaElement) {
    if (!reg.media.has(this)) {
      reg.media.add(this);
      this.addEventListener("play", onPlay);
    }
    return play.call(this);
  };
}

function wrapContexts(win: Win, reg: FrameAudio) {
  const note = (ctx: BaseAudioContext) => {
    reg.contexts.add(ctx);
    if (reg.held > 0 && ctx.state === "running" && "suspend" in ctx) {
      reg.quieted.add(ctx);
      void (ctx as AudioContext).suspend();
    }
  };
  const Ctx = win.AudioContext;
  if (Ctx) {
    win.AudioContext = class extends Ctx {
      constructor(...args: ConstructorParameters<typeof AudioContext>) {
        super(...args);
        note(this);
      }
    };
    const resume = Ctx.prototype.resume;
    Ctx.prototype.resume = function (this: AudioContext) {
      reg.contexts.add(this);
      if (reg.held > 0) return Promise.resolve();
      reg.quieted.delete(this);
      return resume.call(this);
    };
  }
  const Source = win.AudioScheduledSourceNode?.prototype;
  if (Source) {
    const start = Source.start;
    Source.start = function (this: AudioScheduledSourceNode, ...args: Parameters<AudioScheduledSourceNode["start"]>) {
      const ctx = this.context;
      if (reg.held === 0 && reg.quieted.delete(ctx) && Ctx) void resume(ctx);
      note(ctx);
      return start.apply(this, args);
    };
  }
  function resume(ctx: BaseAudioContext) {
    return Ctx ? Ctx.prototype.resume.call(ctx as AudioContext) : Promise.resolve();
  }
}

/** Gives `doc`'s window its registry, once. */
export function trackFrameAudio(doc: Document): () => void {
  const win = doc.defaultView as Win | null;
  if (!win || registries.has(win)) return () => {};
  const reg: FrameAudio = { media: new Set(), contexts: new Set(), quieted: new Set(), held: 0 };
  registries.set(win, reg);
  try {
    wrapPlay(win, reg);
    wrapContexts(win, reg);
  } catch {
    // A window that will not take the wrappers keeps the element hold only.
  }
  return () => {};
}

/** Pauses every remembered player in `win` and suspends its running
 *  contexts, until the returned release is called. */
export function holdFrameAudio(win: Window): () => void {
  const reg = registries.get(win);
  if (!reg) return () => {};
  reg.held += 1;
  for (const m of reg.media) if (!m.paused) m.pause();
  for (const ctx of reg.contexts) {
    if (ctx.state === "running" && "suspend" in ctx) {
      reg.quieted.add(ctx);
      void (ctx as AudioContext).suspend();
    }
  }
  return () => { reg.held = Math.max(0, reg.held - 1); };
}
