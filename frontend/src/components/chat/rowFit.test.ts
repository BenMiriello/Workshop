import { describe, expect, it } from "vitest";
import { FIT_LAST, pickFit } from "./rowFit";

describe("pickFit", () => {
  it("keeps everything when the row fits", () => {
    expect(pickFit(() => false)).toBe(0);
  });

  it("takes the first level that stops overflowing", () => {
    expect(pickFit((l) => l < 3)).toBe(3);
  });

  it("settles on the last level when nothing fits", () => {
    expect(pickFit(() => true)).toBe(FIT_LAST);
  });

  it("stops at a row's own last level", () => {
    expect(pickFit(() => true, 2)).toBe(2);
  });
});
