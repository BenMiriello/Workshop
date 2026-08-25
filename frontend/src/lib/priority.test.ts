import { describe, it, expect } from "vitest";
import { bandOf, floorOf, priorityLabel, PRIORITY_BANDS } from "./priority";

describe("priority bands", () => {
  it("maps an integer to its band", () => {
    expect(bandOf(100)).toBe("urgent");
    expect(bandOf(80)).toBe("high");
    expect(bandOf(40)).toBe("medium");
    expect(bandOf(1)).toBe("low");
    expect(bandOf(0)).toBe("none");
  });

  it("treats a value inside a band as that band, which is the whole point", () => {
    // 78 and 40 were never different priorities. This is the collapse that
    // surprised five agents in one day.
    expect(bandOf(78)).toBe("medium");
    expect(bandOf(40)).toBe("medium");
    expect(bandOf(92)).toBe("high");
    expect(bandOf(80)).toBe("high");
  });

  it("round-trips a band through its floor", () => {
    for (const band of PRIORITY_BANDS) {
      expect(bandOf(floorOf(band))).toBe(band);
    }
  });

  it("is defensive about junk", () => {
    expect(bandOf(null)).toBe("none");
    expect(bandOf(undefined)).toBe("none");
    expect(bandOf("not a number")).toBe("none");
    expect(floorOf("nonsense")).toBe(0);
  });

  it("shows nothing for none rather than the word none", () => {
    expect(priorityLabel(0)).toBe("");
    expect(priorityLabel(80)).toBe("high");
  });
});
