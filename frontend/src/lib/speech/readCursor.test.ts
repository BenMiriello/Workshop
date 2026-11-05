import { describe, expect, it } from "vitest";
import { positionLabel, RESTART_AFTER_S, skipTarget } from "./readCursor";

describe("skipTarget", () => {
  it("moves to the next sentence", () => {
    expect(skipTarget(2, 5, 1, 0)).toBe(3);
  });

  it("has no next sentence at the last one", () => {
    expect(skipTarget(4, 5, 1, 0)).toBeNull();
  });

  it("goes to the previous sentence near the start of this one", () => {
    expect(skipTarget(2, 5, -1, 0.5)).toBe(1);
    expect(skipTarget(2, 5, -1, RESTART_AFTER_S)).toBe(1);
  });

  it("restarts this sentence once the voice is a moment in", () => {
    expect(skipTarget(2, 5, -1, RESTART_AFTER_S + 0.1)).toBe(2);
  });

  it("restarts the first sentence rather than going before it", () => {
    expect(skipTarget(0, 5, -1, 0)).toBe(0);
  });

  it("clamps a long skip to the ends", () => {
    expect(skipTarget(1, 5, -3, 0)).toBe(0);
    expect(skipTarget(1, 5, 10, 0)).toBe(4);
  });

  it("does nothing without sentences or without a move", () => {
    expect(skipTarget(0, 0, 1, 0)).toBeNull();
    expect(skipTarget(2, 5, 0, 0)).toBeNull();
  });
});

describe("positionLabel", () => {
  it("counts from one", () => {
    expect(positionLabel(0, 12)).toBe("1 / 12");
    expect(positionLabel(11, 12)).toBe("12 / 12");
  });

  it("is empty with nothing to read", () => {
    expect(positionLabel(0, 0)).toBe("");
  });
});
