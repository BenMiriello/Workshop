import { describe, it, expect } from "vitest";
import { contextLabel, contextReading, contextTooltip } from "./contextReading";

describe("contextReading", () => {
  // THE METER NEVER DISAPPEARS. These two used to assert `toBeNull()`, and the
  // meter returned null with the reading, so on a new chat the whole control was
  // missing from the composer: absent at zero, appearing when a character was
  // typed, gone again when it was deleted. Operator, 2026-08-14: removing the
  // element is never wanted.
  it("reads zero as zero rather than as nothing", () => {
    expect(contextReading(0, 1_000_000).label).toBe("0%");
    expect(contextReading(0, 1_000_000).fraction).toBe(0);
  });

  it("still reads as zero when the window is unknown, rather than dividing by it", () => {
    expect(contextReading(5_000, 0).label).toBe("0%");
    expect(contextReading(5_000, 0).fraction).toBe(0);
  });

  it("treats a nonsense count as zero instead of printing NaN", () => {
    expect(contextReading(Number.NaN, 1_000_000).label).toBe("0%");
    expect(contextReading(-5, 1_000_000).label).toBe("0%");
  });

  it("rounds a tiny real reading to the nearest whole percent", () => {
    // Operator directive: round, do not floor to "<1%". A real count on a 1M
    // window that rounds to zero simply reads "0%".
    expect(contextReading(1_200, 1_000_000).label).toBe("0%");
  });

  it("rounds at the boundary the way nearest-integer rounding does", () => {
    expect(contextReading(4_999, 1_000_000).label).toBe("0%");
    expect(contextReading(5_000, 1_000_000).label).toBe("1%");
  });

  it("reports an ordinary reading as a whole percent", () => {
    expect(contextReading(410_000, 1_000_000).label).toBe("41%");
  });

  it("clamps a count that has outgrown its window", () => {
    const r = contextReading(2_000_000, 1_000_000);
    expect(r.label).toBe("100%");
    expect(r.fraction).toBe(1);
  });

  it("draws the arc from the true ratio, not the rounded percent", () => {
    // A reading that displays "0%" still shows a sliver from the true fraction.
    expect(contextReading(1_200, 1_000_000).fraction).toBeCloseTo(0.0012, 6);
  });
});

describe("the tooltip", () => {
  // TWO NUMBERS AND A SLASH. It used to be a sentence or two naming where the
  // count came from and what an estimate could not see. Operator, 2026-08-14:
  // "it can just be the number of tokens currently used slash the total number
  // of tokens." These assert the absence of the prose as much as the format,
  // because the prose is what was being objected to.
  it("is used over total, and nothing else", () => {
    expect(contextTooltip(120_000, 1_000_000)).toBe("120,000 / 1,000,000");
  });

  it("says nothing about where the number came from", () => {
    const t = contextTooltip(120_000, 1_000_000);
    expect(t).not.toMatch(/server|estimate|browser|persona|context|token/i);
  });

  it("survives a missing count without printing NaN", () => {
    expect(contextTooltip(Number.NaN, 1_000_000)).toBe("0 / 1,000,000");
  });

  it("renders a plain percent beside it", () => {
    expect(contextLabel(contextReading(120_000, 1_000_000))).toBe("12%");
  });
});
