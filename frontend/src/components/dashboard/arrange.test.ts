import { describe, expect, it } from "vitest";
import { move, orderBy, pressIsTap, TAP_MS } from "./arrange";

const id = (s: string) => s;

describe("arranged order", () => {
  it("puts saved ids first, in the saved order", () => {
    expect(orderBy(["a", "b", "c", "d"], ["c", "a"], id)).toEqual(["c", "a", "b", "d"]);
  });
  it("skips saved ids that are not shown", () => {
    expect(orderBy(["a", "b"], ["hidden", "b"], id)).toEqual(["b", "a"]);
  });
  it("moves one item and keeps the rest in order", () => {
    expect(move(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(move(["a", "b", "c", "d"], 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(move(["a", "b"], 0, 5)).toEqual(["a", "b"]);
  });
});

describe("a press on a tile", () => {
  it("is a tap when it never moved far enough to drag", () => {
    expect(pressIsTap(false, true, 2000)).toBe(true);
  });
  it("is a tap when it wobbled but was quick and ended on its own place", () => {
    expect(pressIsTap(true, true, TAP_MS - 1)).toBe(true);
  });
  it("is a drag when it was slow or ended somewhere else", () => {
    expect(pressIsTap(true, true, TAP_MS)).toBe(false);
    expect(pressIsTap(true, false, 50)).toBe(false);
  });
});
