import { describe, it, expect } from "vitest";
import { lineBottoms, cutHeight, worthClamping } from "./clampCut";

/** Rects as the DOM reports them: only bottom and height matter here. */
const r = (bottom: number, height = 16) => ({ bottom, height });

describe("lineBottoms", () => {
  it("reports one bottom per line", () => {
    expect(lineBottoms([r(16), r(32), r(48)])).toEqual([16, 32, 48]);
  });

  it("folds the several rects of one line into one", () => {
    // A line containing a link and a code span arrives as three rects sharing
    // a bottom. Counting them as three lines would cut after a third of a line.
    expect(lineBottoms([r(16), r(16), r(16), r(32)])).toEqual([16, 32]);
  });

  it("merges only near-identical bottoms, and counts anything else as its own box", () => {
    // Sub-pixel apart: the same line, rounded differently. Merged.
    expect(lineBottoms([r(16), r(16.3), r(32)])).toEqual([16.3, 32]);
    // Several pixels apart (an inline image overhanging its line): counted
    // separately, ON PURPOSE. The tolerance stays tight because merging two
    // genuinely distinct boxes could hide a real line, whereas counting one
    // box twice only cuts slightly earlier, still on a true boundary. Never
    // cutting inside a box is the property that matters.
    expect(lineBottoms([r(16), r(20, 20), r(36)])).toEqual([16, 20, 36]);
  });

  it("ignores zero-height rects", () => {
    expect(lineBottoms([r(16), { bottom: 20, height: 0 }, r(32)])).toEqual([16, 32]);
  });

  it("ignores a rect that runs backwards rather than reordering", () => {
    // A float can report above the previous line. It belongs to a line already
    // counted; sorting instead would merge two real lines into one.
    expect(lineBottoms([r(16), r(32), r(20), r(48)])).toEqual([16, 32, 48]);
  });

  it("is total on nothing", () => {
    expect(lineBottoms([])).toEqual([]);
  });
});

describe("cutHeight", () => {
  it("ends exactly at the bottom of the Nth line", () => {
    expect(cutHeight([16, 32, 48, 64], 0, 2)).toBe(32);
  });

  it("is measured from the block's own top, not the viewport", () => {
    // The rects are viewport coordinates; the height is not.
    expect(cutHeight([116, 132, 148, 164], 100, 3)).toBe(48);
  });

  it("rounds UP, because rounding down shaves the last row of text", () => {
    expect(cutHeight([16.4, 32.4, 48.4], 0, 2)).toBe(33);
  });

  it("cuts on the real line, not on a multiple of the line height", () => {
    // The whole point. A heading then two paragraphs: nothing here sits on a
    // 16px grid, and `calc(2 * 1lh)` would have landed at 32, inside line two.
    expect(cutHeight([24, 46, 62, 78], 0, 2)).toBe(46);
  });

  it("says there is nothing to cut when everything fits", () => {
    expect(cutHeight([16, 32], 0, 2)).toBeNull();
    expect(cutHeight([16, 32], 0, 5)).toBeNull();
  });

  it("refuses a nonsense line count rather than returning a negative height", () => {
    expect(cutHeight([16, 32, 48], 0, 0)).toBeNull();
    expect(cutHeight([16, 32, 48], 999, 1)).toBe(0);
  });
});

describe("worthClamping", () => {
  it("needs 2+ lines to hide", () => {
    // Hiding one line and adding a Show more row in its place saves nothing.
    expect(worthClamping(11, 10)).toBe(false);
    expect(worthClamping(12, 10)).toBe(true);
    expect(worthClamping(4, 10)).toBe(false);
  });
});
