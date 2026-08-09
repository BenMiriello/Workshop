import { describe, expect, it } from "vitest";
import { contextCutMark } from "./contextCut";

describe("contextCutMark", () => {
  it("draws nothing when nothing has been cut", () => {
    expect(contextCutMark(0, 10)).toBeNull();
  });

  it("draws inline at the boundary when a tail survives (a forced /compact)", () => {
    expect(contextCutMark(6, 10)).toEqual({ atIndex: 6 });
  });

  it("draws trailing when the cut reaches the end (a /clear)", () => {
    expect(contextCutMark(10, 10)).toEqual({ trailing: true });
  });

  it("draws trailing rather than silently nothing if the boundary overshoots", () => {
    expect(contextCutMark(11, 10)).toEqual({ trailing: true });
  });
});
