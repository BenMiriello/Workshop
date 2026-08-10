import { describe, it, expect } from "vitest";
import { contextLabel, contextReading, contextTooltip } from "./contextReading";

describe("contextReading", () => {
  it("renders nothing when there is no reading", () => {
    expect(contextReading(0, 1_000_000)).toBeNull();
  });

  it("renders nothing rather than dividing by an unknown window", () => {
    expect(contextReading(5_000, 0)).toBeNull();
  });

  it("never says 0% about a chat that is using context", () => {
    // The exact case the operator hit: a real count, a 1M window, rounds to 0.
    const r = contextReading(1_200, 1_000_000);
    expect(r?.label).toBe("<1%");
  });

  it("floors at the boundary, so nothing between nothing and one percent is 0%", () => {
    expect(contextReading(4_999, 1_000_000)?.label).toBe("<1%");
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
    // "<1%" still shows a sliver; a rounded 0 would show nothing.
    expect(contextReading(1_200, 1_000_000)?.fraction).toBeCloseTo(0.0012, 6);
  });
});

describe("where the number came from", () => {
  const r = contextReading(120_000, 1_000_000)!;

  // The bug: a failed /context read was swallowed and the browser's own
  // character estimate rendered as though the server had counted it.
  it("marks an estimate and leaves the server's own count unmarked", () => {
    expect(contextLabel(r, "server")).toBe("12%");
    expect(contextLabel(r, "estimate")).toBe("~12%");
    expect(contextLabel(r, "unavailable")).toBe("~12%");
  });

  it("marks a floored reading too, so <1% cannot pass as measured", () => {
    const tiny = contextReading(10, 1_000_000)!;
    expect(contextLabel(tiny, "unavailable")).toBe("~<1%");
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
