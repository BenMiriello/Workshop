import { describe, it, expect } from "vitest";
import {
  CAP_AFTER, CAP_ROWS, BOTTOM_EPSILON,
  isCapped, atBottom, bottomOf, shouldFollow, onReaderScroll,
} from "./stepScroll";

// The rule these pin is an explicit failure condition, not a nicety: a new step
// arriving must never yank a reader who has scrolled up back to the bottom.

describe("isCapped", () => {
  it("leaves ordinary groups alone", () => {
    // The median run is 2 steps. Those must render exactly as they always did.
    expect(isCapped(1)).toBe(false);
    expect(isCapped(2)).toBe(false);
    expect(isCapped(CAP_AFTER)).toBe(false);
  });

  it("caps only past the threshold", () => {
    expect(isCapped(CAP_AFTER + 1)).toBe(true);
    expect(isCapped(173)).toBe(true); // the longest run in the operator's logs
  });

  it("is one row taller than the threshold, so a partial row always shows", () => {
    expect(CAP_ROWS).toBe(CAP_AFTER + 1);
  });
});

describe("atBottom", () => {
  it("is true at the exact bottom", () => {
    expect(atBottom({ scrollTop: 100, clientHeight: 200, scrollHeight: 300 })).toBe(true);
  });

  it("is true for a region too short to scroll", () => {
    // No 'up' to have scrolled to, so it follows by default as it fills.
    expect(atBottom({ scrollTop: 0, clientHeight: 200, scrollHeight: 120 })).toBe(true);
  });

  it("tolerates sub-pixel drift rather than reading it as a scroll-up", () => {
    // A hair short of the bottom is layout noise, not the reader moving. Reading
    // it as a scroll-up silently turns following off for someone who never
    // touched anything.
    expect(atBottom({ scrollTop: 100 - BOTTOM_EPSILON, clientHeight: 200, scrollHeight: 300 })).toBe(true);
  });

  it("is false once the reader has genuinely scrolled up", () => {
    expect(atBottom({ scrollTop: 40, clientHeight: 200, scrollHeight: 300 })).toBe(false);
  });
});

describe("bottomOf", () => {
  it("is the max offset", () => {
    expect(bottomOf({ scrollTop: 0, clientHeight: 200, scrollHeight: 300 })).toBe(100);
  });

  it("never goes negative for content shorter than the viewport", () => {
    expect(bottomOf({ scrollTop: 0, clientHeight: 200, scrollHeight: 120 })).toBe(0);
  });
});

describe("shouldFollow", () => {
  it("follows while the reader is at the bottom", () => {
    expect(shouldFollow(true)).toBe(true);
  });

  it("DOES NOT yank a reader who scrolled up", () => {
    // The whole point. Operator, 2026-08-27: "it shouldn't just keep resetting
    // you back down to the bottom".
    expect(shouldFollow(false)).toBe(false);
  });

  it("consults the state sampled BEFORE the content grew, not the metrics after", () => {
    // The classic inversion: after new content lands, a reader who never moved
    // is no longer at the bottom *because the content grew*. Recomputing from
    // post-growth metrics would answer "not following" and turn it off once,
    // permanently, on the very first new step. shouldFollow takes no metrics at
    // all, which is what makes that mistake unrepresentable here.
    const wasAtBottom = atBottom({ scrollTop: 100, clientHeight: 200, scrollHeight: 300 });
    // ...a step arrives, content is now 400 tall, offset unchanged at 100.
    const afterGrowth = { scrollTop: 100, clientHeight: 200, scrollHeight: 400 };
    expect(atBottom(afterGrowth)).toBe(false);       // metrics alone say "moved"
    expect(shouldFollow(wasAtBottom)).toBe(true);    // the rule says otherwise
  });
});

describe("onReaderScroll", () => {
  it("stops following when the reader scrolls up", () => {
    expect(onReaderScroll({ scrollTop: 20, clientHeight: 200, scrollHeight: 300 })).toBe(false);
  });

  it("resumes following when they scroll back to the bottom", () => {
    // No button and no timeout: arriving at the newest step is the whole gesture.
    expect(onReaderScroll({ scrollTop: 100, clientHeight: 200, scrollHeight: 300 })).toBe(true);
  });

  it("round-trips: bottom -> up -> bottom", () => {
    const m = (scrollTop: number) => ({ scrollTop, clientHeight: 200, scrollHeight: 300 });
    let following = onReaderScroll(m(100));
    expect(following).toBe(true);
    following = onReaderScroll(m(30));
    expect(following).toBe(false);
    // A step arrives while they are up there. Nothing moves.
    expect(shouldFollow(following)).toBe(false);
    following = onReaderScroll(m(100));
    expect(following).toBe(true);
  });
});
