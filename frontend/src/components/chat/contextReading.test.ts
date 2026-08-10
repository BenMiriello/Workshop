import { describe, it, expect } from "vitest";
import { contextReading } from "./contextReading";

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
