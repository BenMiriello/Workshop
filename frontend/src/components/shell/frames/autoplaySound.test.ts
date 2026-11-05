import { describe, expect, it } from "vitest";
import { quietAutoplay, quietOnStart, type PlayerState } from "./autoplaySound";

const base: PlayerState = { autoplay: true, muted: false, volume: 1, stream: false, allowed: false };

describe("quietOnStart", () => {
  it("quiets a player that started by itself with sound", () => {
    expect(quietOnStart(base)).toBe(true);
  });

  it("leaves playback started from code, muted players, streams and unmuted ones", () => {
    expect(quietOnStart({ ...base, autoplay: false })).toBe(false);
    expect(quietOnStart({ ...base, muted: true })).toBe(false);
    expect(quietOnStart({ ...base, volume: 0 })).toBe(false);
    expect(quietOnStart({ ...base, stream: true })).toBe(false);
    expect(quietOnStart({ ...base, allowed: true })).toBe(false);
  });
});

class FakeMedia {
  autoplay = true;
  muted = false;
  volume = 1;
  paused = true;
  srcObject = null;
  currentSrc = "a.mp4";
  constructor(public tagName: string) {}
  pause() { this.paused = true; }
}

function fakeDocument(media: FakeMedia[]) {
  const listeners = new Map<string, Set<(e: Event) => void>>();
  const doc = {
    querySelectorAll: () => media,
    addEventListener: (k: string, fn: (e: Event) => void) => {
      if (!listeners.has(k)) listeners.set(k, new Set());
      listeners.get(k)!.add(fn);
    },
    removeEventListener: (k: string, fn: (e: Event) => void) => listeners.get(k)?.delete(fn),
  };
  const fire = (k: string, m: FakeMedia) =>
    listeners.get(k)?.forEach((l) => l({ target: m } as unknown as Event));
  return { doc: doc as unknown as Document, fire };
}

describe("quietAutoplay", () => {
  it("mutes a video that autoplays and keeps it playing", () => {
    const v = new FakeMedia("VIDEO");
    const { doc, fire } = fakeDocument([v]);
    quietAutoplay(doc);
    v.paused = false;
    fire("play", v);
    expect(v.muted).toBe(true);
    expect(v.paused).toBe(false);
  });

  it("pauses an audio player that autoplays", () => {
    const a = new FakeMedia("AUDIO");
    const { doc, fire } = fakeDocument([a]);
    quietAutoplay(doc);
    a.paused = false;
    fire("play", a);
    expect(a.paused).toBe(true);
  });

  it("keeps sound once unmuted, until the source changes", () => {
    const v = new FakeMedia("VIDEO");
    const { doc, fire } = fakeDocument([v]);
    quietAutoplay(doc);
    fire("play", v);
    v.muted = false;
    fire("volumechange", v);
    fire("play", v);
    expect(v.muted).toBe(false);
    v.currentSrc = "b.mp4";
    fire("play", v);
    expect(v.muted).toBe(true);
  });

  it("quiets what is already playing when it attaches", () => {
    const v = new FakeMedia("VIDEO");
    v.paused = false;
    quietAutoplay(fakeDocument([v]).doc);
    expect(v.muted).toBe(true);
  });
});
