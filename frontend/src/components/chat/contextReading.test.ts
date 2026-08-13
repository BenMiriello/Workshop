import { describe, it, expect } from "vitest";
import { contextLabel, contextReading, contextTooltip } from "./contextReading";

describe("contextReading", () => {
  it("renders nothing when there is no reading", () => {
    expect(contextReading(0, 1_000_000)).toBeNull();
  });

  it("renders nothing rather than dividing by an unknown window", () => {
    expect(contextReading(5_000, 0)).toBeNull();
  });

  it("rounds a tiny real reading to the nearest whole percent", () => {
    // Operator directive: round, do not floor to "<1%". A real count on a 1M
    // window that rounds to zero simply reads "0%".
    expect(contextReading(1_200, 1_000_000)?.label).toBe("0%");
  });

  it("rounds at the boundary the way nearest-integer rounding does", () => {
    expect(contextReading(4_999, 1_000_000)?.label).toBe("0%");
    expect(contextReading(5_000, 1_000_000)?.label).toBe("1%");
  });

  it("reports an ordinary reading as a whole percent", () => {
    expect(contextReading(410_000, 1_000_000)?.label).toBe("41%");
  });

  it("clamps a count that has outgrown its window", () => {
    const r = contextReading(2_000_000, 1_000_000);
    expect(r?.label).toBe("100%");
    expect(r?.fraction).toBe(1);
  });

  it("draws the arc from the true ratio, not the rounded percent", () => {
    // A reading that displays "0%" still shows a sliver from the true fraction.
    expect(contextReading(1_200, 1_000_000)?.fraction).toBeCloseTo(0.0012, 6);
  });
});

describe("where the number came from", () => {
  const r = contextReading(120_000, 1_000_000)!;

  // The label is a plain percent and does not vary by source at all; the tilde
  // that used to mark an estimate was clutter on every new chat (operator,
  // 2026-08-11). It no longer even takes the source: the measured-vs-guessed
  // distinction lives in the tooltip, asserted below.
  it("renders a plain percent", () => {
    expect(contextLabel(r)).toBe("12%");
  });

  it("says the server counted it only when the server did", () => {
    expect(contextTooltip(120_000, 1_000_000, "server")).toMatch(/counted by the server/);
    expect(contextTooltip(120_000, 1_000_000, "estimate")).not.toMatch(/counted by the server/);
  });

  it("distinguishes a read that has not happened from one that failed", () => {
    expect(contextTooltip(1, 100, "unavailable")).toMatch(/could not be read/);
    expect(contextTooltip(1, 100, "estimate")).not.toMatch(/could not be read/);
  });

  it("says what an estimate cannot see, since that is why it differs", () => {
    for (const s of ["estimate", "unavailable"] as const) {
      expect(contextTooltip(1, 100, s)).toMatch(/persona|live context/);
    }
  });
});
