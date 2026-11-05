import { describe, expect, it } from "vitest";
import { holdMedia } from "./frameMedia";

class FakeMedia {
  paused: boolean;
  constructor(playing: boolean) { this.paused = !playing; }
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
}

function fakeWindow(media: FakeMedia[], frames: unknown[] = []) {
  const listeners = new Set<(e: Event) => void>();
  const document = {
    querySelectorAll: () => media,
    addEventListener: (_: string, fn: (e: Event) => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: (e: Event) => void) => listeners.delete(fn),
  };
  const win = { document, frames: Object.assign(frames, { length: frames.length }) };
  const started = (m: FakeMedia) => { m.paused = false; listeners.forEach((l) => l({ target: m } as unknown as Event)); };
  return { win: win as unknown as Window, started, listeners };
}

describe("holdMedia", () => {
  it("pauses what plays and leaves it paused on release", () => {
    const playing = new FakeMedia(true);
    const { win } = fakeWindow([playing]);
    const release = holdMedia(win);
    expect(playing.paused).toBe(true);
    release();
    expect(playing.paused).toBe(true);
  });

  it("pauses media that starts while held, and lets it play after release", () => {
    const later = new FakeMedia(false);
    const { win, started, listeners } = fakeWindow([later]);
    const release = holdMedia(win);
    started(later);
    expect(later.paused).toBe(true);
    release();
    expect(listeners.size).toBe(0);
    started(later);
    expect(later.paused).toBe(false);
  });

  it("reaches media in frames inside the frame", () => {
    const inner = new FakeMedia(true);
    const child = fakeWindow([inner]).win;
    const { win } = fakeWindow([], [child]);
    holdMedia(win);
    expect(inner.paused).toBe(true);
  });
});
