import { describe, it, expect } from "vitest";
import { restoresToBottom, FOLLOW_SLOP, type ScrollPos } from "./scrollRestore";

// A 1000px-tall viewport over a 5000px transcript: max scrollTop is 4000.
const H = 5000;
const V = 1000;
const at = (top: number, atBottom = false): ScrollPos => ({ top, atBottom });

describe("restoresToBottom", () => {
  it("nothing saved means the latest", () => {
    expect(restoresToBottom(null, H, V)).toBe(true);
  });

  it("saved at the bottom means the latest", () => {
    expect(restoresToBottom(at(4000, true), H, V)).toBe(true);
  });

  it("within half a view height of the bottom counts as the bottom", () => {
    // 300px up from the bottom: well inside the 500px half-viewport.
    expect(restoresToBottom(at(3700), H, V)).toBe(true);
    // 499px up: still inside.
    expect(restoresToBottom(at(3501), H, V)).toBe(true);
  });

  it("further than half a view height keeps the saved position", () => {
    expect(restoresToBottom(at(3500), H, V)).toBe(false);  // exactly 500, not <
    expect(restoresToBottom(at(1000), H, V)).toBe(false);
    expect(restoresToBottom(at(0), H, V)).toBe(false);
  });

  it("the threshold scales with the viewport, not with a fixed number", () => {
    // 300px from the bottom is "the bottom" in a tall window and genuinely
    // scrolled-up in a short one. This is the whole reason it is not a constant.
    expect(restoresToBottom(at(3700), H, 1000)).toBe(true);
    expect(restoresToBottom(at(H - 400 - 400), H, 400)).toBe(false);
  });

  it("is far looser than the follow threshold, deliberately", () => {
    // The exact case the old code got wrong: scrolled up more than the follow
    // slop, so it was saved atBottom:false, but obviously still reading the end.
    const justOutsideFollow = at(4000 - (FOLLOW_SLOP + 20));
    expect(restoresToBottom(justOutsideFollow, H, V)).toBe(true);
  });

  it("survives a saved offset that outran the content", () => {
    // The growth case: saved deep into a thread that is now shorter (or is not
    // fully rendered yet). Clamped, then measured, never NaN or negative.
    expect(restoresToBottom(at(99999), H, V)).toBe(true);
    expect(restoresToBottom(at(-50), H, V)).toBe(false);
  });

  it("a transcript shorter than its viewport is always the bottom", () => {
    expect(restoresToBottom(at(0), 400, V)).toBe(true);
  });
});
