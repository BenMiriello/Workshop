import { describe, expect, it } from "vitest";
import { liveUrl, parseLiveMessage, PendingAudio } from "./liveWire";

const pcm = (n: number) => new Int16Array(n);

describe("parseLiveMessage", () => {
  it("reads the server's messages and drops anything else", () => {
    expect(parseLiveMessage({ t: "final", text: "Hi.", from: 0, to: 3200, rest: "And", restTo: 4800 }))
      .toEqual({ t: "final", text: "Hi.", from: 0, to: 3200, rest: "And", restTo: 4800 });
    expect(parseLiveMessage({ t: "final", text: "Hi.", from: 0, to: 3200 }))
      .toEqual({ t: "final", text: "Hi.", from: 0, to: 3200, rest: "", restTo: 3200 });
    expect(parseLiveMessage({ t: "text", text: "Hi", from: 0, to: 1600 })).toEqual({ t: "text", text: "Hi", from: 0, to: 1600 });
    expect(parseLiveMessage({ t: "text", text: "no offsets" })).toBeNull();
    expect(parseLiveMessage({ t: "final", text: "no offset", from: 0 })).toBeNull();
    expect(parseLiveMessage({ t: "state" })).toBeNull();
    expect(parseLiveMessage({ t: "command", name: "send", words: "Send message.", from: 10, to: 20 }))
      .toEqual({ t: "command", name: "send", arg: null, words: "Send message.", from: 10, to: 20 });
    expect(parseLiveMessage({ t: "command", name: "chat_model", arg: " opus ", from: 1, to: 2 }))
      .toMatchObject({ arg: "opus", words: "" });
    expect(parseLiveMessage({ t: "command", from: 1, to: 2 })).toBeNull();
    expect(parseLiveMessage({ t: "paused", paused: true })).toEqual({ t: "paused", paused: true });
    expect(parseLiveMessage("final")).toBeNull();
  });
});

describe("liveUrl", () => {
  it("follows the page's scheme", () => {
    expect(liveUrl({ protocol: "https:", host: "h:1" })).toBe("wss://h:1/api/speech/live");
    expect(liveUrl({ protocol: "http:", host: "h" })).toBe("ws://h/api/speech/live");
  });
});

describe("PendingAudio", () => {
  it("keeps what is not settled and resumes from the settled point", () => {
    const p = new PendingAudio(10_000);
    p.push(0, pcm(320));
    p.push(320, pcm(320));
    p.push(640, pcm(320));
    p.settle(640);
    expect(p.all().map((f) => f.cursor)).toEqual([640]);
    expect(p.from).toBe(640);
    expect(p.samples).toBe(320);
  });

  it("keeps a frame the settled point falls inside", () => {
    const p = new PendingAudio(10_000);
    p.push(0, pcm(320));
    p.settle(100);
    expect(p.all()).toHaveLength(1);
    expect(p.from).toBe(100);
  });

  it("ignores audio that is already settled", () => {
    const p = new PendingAudio(10_000);
    p.settle(1000);
    p.push(0, pcm(320));
    expect(p.samples).toBe(0);
    expect(p.from).toBe(1000);
  });

  it("lets the oldest audio go past its bound", () => {
    const p = new PendingAudio(640);
    for (let i = 0; i < 5; i++) p.push(i * 320, pcm(320));
    expect(p.samples).toBe(640);
    expect(p.from).toBe(960);
  });

  it("reads what a sentence worded freely most likely meant", () => {
    const m = parseLiveMessage({ t: "command", name: "guess", arg: null, words: "move down", from: 1, to: 2,
      guesses: [{ name: "scroll_down", p: 0.8 }, { name: "type", p: 0.1, text: "Best" }, { bad: 1 }], none: 0.05 });
    expect(m).toEqual({ t: "command", name: "guess", arg: null, words: "move down", from: 1, to: 2,
      guesses: [{ name: "scroll_down", p: 0.8 }, { name: "type", p: 0.1, text: "Best" }], none: 0.05, loading: false });
    const loading = parseLiveMessage({ t: "command", name: "guess", words: "x", from: 1, to: 2, loading: true });
    expect(loading).toMatchObject({ name: "guess", guesses: [], none: 1, loading: true });
  });
});
