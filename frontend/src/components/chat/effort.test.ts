import { describe, it, expect } from "vitest";
import { effortLabel, nearestEffort, orderEfforts } from "./effort";

describe("effort labels", () => {
  it("names the levels", () => {
    expect(effortLabel("xhigh")).toBe("Extra high");
    expect(effortLabel("max")).toBe("Max");
  });

  it("falls back to the raw id for a level it has never heard of", () => {
    // A level the backend adds must stay usable before this table knows its
    // name; vanishing would be worse than reading slightly raw.
    expect(effortLabel("ludicrous")).toBe("ludicrous");
  });
});

describe("orderEfforts", () => {
  it("sorts weakest first regardless of input order", () => {
    expect(orderEfforts(["max", "low", "high"])).toEqual(["low", "high", "max"]);
  });

  it("keeps an unknown level, at the end", () => {
    expect(orderEfforts(["ludicrous", "low"])).toEqual(["low", "ludicrous"]);
  });
});

describe("nearestEffort", () => {
  const sonnet46 = ["low", "medium", "high", "max"];   // real: max, no xhigh
  const opus5 = ["low", "medium", "high", "xhigh", "max"];

  it("keeps a level the model supports", () => {
    expect(nearestEffort("max", sonnet46)).toBe("max");
  });

  it("steps DOWN when the model lacks the level", () => {
    // xhigh -> high, not max. Switching model must never silently cost more
    // than the operator asked for.
    expect(nearestEffort("xhigh", sonnet46)).toBe("high");
  });

  it("steps up only when there is nothing at or below", () => {
    expect(nearestEffort("low", ["high", "max"])).toBe("high");
  });

  it("returns empty for a model with no effort control", () => {
    expect(nearestEffort("high", [])).toBe("");
  });

  it("returns empty for a level that is not a level", () => {
    expect(nearestEffort("ludicrous", opus5)).toBe("");
  });
});
