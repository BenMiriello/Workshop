import { describe, expect, it } from "vitest";
import { MIC_RATE } from "../../../lib/voice/capture";
import { answered, type TakeBack } from "./takeBacks";

const s = (sec: number) => sec * MIC_RATE;
const op = (at: number | null, made = 0): TakeBack => ({ at, made });

describe("which operation a spoken cancel answers", () => {
  it("is the one said just before it, within five seconds of audio", () => {
    const send = op(s(10));
    expect(answered([send], { from: s(12), heardAt: 999_999 })).toBe(send);
    expect(answered([send], { from: s(15.5), heardAt: 0 })).toBeNull();
  });

  it("is judged by the audio, so a page that read it late still finds it", () => {
    const send = op(s(10), 1000);
    expect(answered([send], { from: s(12), heardAt: 1000 + 60_000 })).toBe(send);
  });

  it("never reaches a send made after it was said", () => {
    const first = op(s(10));
    const later = op(s(20));
    expect(answered([first, later], { from: s(12), heardAt: 0 })).toBe(first);
    expect(answered([first, later], { from: s(17), heardAt: 0 })).toBeNull();
  });

  it("times an operation made from a button by the clock", () => {
    const fromMenu = op(null, 1000);
    expect(answered([fromMenu], { from: s(1), heardAt: 4000 })).toBe(fromMenu);
    expect(answered([fromMenu], { from: s(1), heardAt: 7000 })).toBeNull();
  });

});
