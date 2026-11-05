import { describe, expect, it } from "vitest";
import { EDGE_SLOP_PX, gapBelow } from "./frameEdge";

// Measured on iOS 26.5 WebKit: a chat framed in an app's chat page ended at
// 695 in a 778px window, on top of the app's 83px nav bar.
describe("gapBelow", () => {
  it("is the space between a frame's bottom and the window's", () => {
    expect(gapBelow(695, 778)).toBe(83);
  });
  it("is zero for a frame that reaches the bottom (a sheet over the page)", () => {
    expect(gapBelow(778, 778)).toBe(0);
  });
  it("treats a pixel of rounding as reaching it", () => {
    expect(gapBelow(777.4, 778)).toBeLessThanOrEqual(EDGE_SLOP_PX);
  });
  it("with the keyboard up, the window is what is left above it", () => {
    expect(gapBelow(349, 432)).toBe(83);
  });
});
