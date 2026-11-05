import { describe, expect, it } from "vitest";
import { revealDelta } from "./revealCaret";

const box = { top: 100, bottom: 400 };

describe("revealDelta", () => {
  it("leaves a caret that is already in view", () => {
    expect(revealDelta({ top: 200, bottom: 220 }, box, 8)).toBe(0);
  });

  it("scrolls down to a caret the keyboard left below the box", () => {
    expect(revealDelta({ top: 594, bottom: 614 }, box, 8)).toBe(614 - 392);
  });

  it("scrolls up to a caret above the box", () => {
    expect(revealDelta({ top: 50, bottom: 70 }, box, 8)).toBe(50 - 108);
  });

  it("counts the margin as out of view", () => {
    expect(revealDelta({ top: 380, bottom: 396 }, box, 8)).toBe(4);
  });

  it("aligns a caret taller than the box by its top", () => {
    expect(revealDelta({ top: 150, bottom: 600 }, box, 8)).toBe(42);
  });
});
