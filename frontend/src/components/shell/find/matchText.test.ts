import { describe, expect, it } from "vitest";
import { countLabel, matchOffsets, stepIndex } from "./matchText";

describe("matchOffsets", () => {
  it("finds every occurrence regardless of case", () => {
    expect(matchOffsets("Foo foo FOO", "foo")).toEqual([0, 4, 8]);
  });
  it("does not overlap matches", () => {
    expect(matchOffsets("aaaa", "aa")).toEqual([0, 2]);
  });
  it("finds nothing for an empty query", () => {
    expect(matchOffsets("anything", "")).toEqual([]);
  });
  it("skips a string whose lowercase form changes length", () => {
    expect(matchOffsets("İstanbul", "stan")).toEqual([]);
  });
});

describe("stepIndex", () => {
  it("wraps forward and back", () => {
    expect(stepIndex(2, 3, 1)).toBe(0);
    expect(stepIndex(0, 3, -1)).toBe(2);
  });
  it("starts at an end when nothing is current", () => {
    expect(stepIndex(-1, 3, 1)).toBe(0);
    expect(stepIndex(-1, 3, -1)).toBe(2);
  });
  it("answers -1 with no matches", () => {
    expect(stepIndex(0, 0, 1)).toBe(-1);
  });
});

describe("countLabel", () => {
  it("is empty before a query", () => expect(countLabel(-1, 0, "")).toBe(""));
  it("says when nothing matched", () => expect(countLabel(-1, 0, "x")).toBe("No matches"));
  it("counts from one", () => expect(countLabel(0, 12, "x")).toBe("1 of 12"));
});
