import { describe, expect, it } from "vitest";
import { shortSha } from "./sha";

describe("shortSha", () => {
  it("abbreviates to the 7 characters git itself prints", () => {
    expect(shortSha("bd354ffb0c9a1e2d3f4a5b6c7d8e9f0a1b2c3d4e")).toBe("bd354ff");
  });

  it("is empty, never 'undefined', for a missing sha", () => {
    // These render inline in a meta line on truthiness, so the empty string is
    // the contract: `{sha ? shortSha(sha) : ...}` must not print "undefin".
    expect(shortSha(undefined)).toBe("");
    expect(shortSha(null)).toBe("");
    expect(shortSha("")).toBe("");
  });

  it("leaves an already-short ref alone rather than padding it", () => {
    expect(shortSha("main")).toBe("main");
    expect(shortSha("abc1234")).toBe("abc1234");
  });
});
