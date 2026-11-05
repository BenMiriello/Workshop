import { describe, expect, it } from "vitest";
import { chooseAnchor, visiblePart, type AnchorCandidate } from "./anchor";

const at = (left: number, top: number, w: number, h: number, over: Partial<AnchorCandidate> = {}): AnchorCandidate => ({
  box: { left, top, right: left + w, bottom: top + h }, marked: false, visible: true, ...over,
});

describe("chooseAnchor", () => {
  it("keeps an ordinary visible field", () => {
    expect(chooseAnchor([at(10, 10, 300, 32), at(0, 0, 800, 600)])).toBe(0);
  });

  it("moves off a terminal's hidden helper to the terminal area", () => {
    const helper = at(212, 340, 1, 17, { visible: false });
    const row = at(0, 0, 90, 17);
    const screen = at(0, 0, 900, 500);
    expect(chooseAnchor([helper, row, screen])).toBe(2);
  });

  it("moves off a tiny but painted stand-in too", () => {
    expect(chooseAnchor([at(5, 5, 4, 4), at(0, 0, 600, 300)])).toBe(1);
  });

  it("lets an app name the area, nearest mark first", () => {
    const chain = [at(10, 10, 300, 32), at(0, 0, 400, 60, { marked: true }), at(0, 0, 800, 600, { marked: true })];
    expect(chooseAnchor(chain)).toBe(1);
  });

  it("falls back to the field when nothing around it is big enough", () => {
    expect(chooseAnchor([at(5, 5, 4, 4), at(0, 0, 50, 20)])).toBe(0);
  });
});

describe("visiblePart", () => {
  it("clips a tall editor to the screen", () => {
    const view = { left: 0, top: 0, right: 1000, bottom: 800 };
    expect(visiblePart({ left: 100, top: 200, right: 900, bottom: 5000 }, view))
      .toEqual({ left: 100, top: 200, right: 900, bottom: 800 });
    expect(visiblePart({ left: 100, top: 900, right: 900, bottom: 1000 }, view)).toBeNull();
  });
});
