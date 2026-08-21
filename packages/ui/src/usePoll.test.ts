import { describe, it, expect } from "vitest";
import { pollShouldRun } from "./usePoll";

describe("pollShouldRun", () => {
  it("does not run when disabled, whatever else is true", () => {
    expect(pollShouldRun({ enabled: false, hidden: false, whenHidden: "pause" })).toBe(false);
    expect(pollShouldRun({ enabled: false, hidden: false, whenHidden: "continue" })).toBe(false);
    expect(pollShouldRun({ enabled: false, hidden: true, whenHidden: "continue" })).toBe(false);
  });

  it("runs while visible", () => {
    expect(pollShouldRun({ enabled: true, hidden: false, whenHidden: "pause" })).toBe(true);
    expect(pollShouldRun({ enabled: true, hidden: false, whenHidden: "continue" })).toBe(true);
  });

  // The default, and the whole reason this module exists: a backgrounded PWA
  // stops polling.
  it("pauses while hidden by default", () => {
    expect(pollShouldRun({ enabled: true, hidden: true, whenHidden: "pause" })).toBe(false);
  });

  it("keeps running while hidden only when the caller asked for it", () => {
    expect(pollShouldRun({ enabled: true, hidden: true, whenHidden: "continue" })).toBe(true);
  });
});
