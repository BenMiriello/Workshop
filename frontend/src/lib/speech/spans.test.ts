import { describe, expect, it } from "vitest";
import { findIn, spansOf } from "./spans";

describe("a name said inside a sentence", () => {
  it("tries longer spans first, the later one first among equals", () => {
    expect(spansOf("open the speech settings")).toEqual([
      "open the speech settings",
      "the speech settings", "open the speech",
      "speech settings", "the speech", "open the",
      "settings", "speech", "the", "open",
    ]);
  });

  it("finds the longest span a matcher accepts", () => {
    const names = new Set(["speech settings", "settings"]);
    expect(findIn("can you open up the speech settings", (q) => (names.has(q) ? q : null))).toBe("speech settings");
    expect(findIn("nothing here", (q) => (names.has(q) ? q : null))).toBeNull();
  });
});
