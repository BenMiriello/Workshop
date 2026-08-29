import { describe, expect, it } from "vitest";
import { foldControls } from "./foldControls";

describe("foldControls", () => {
  it("offers both actions when nothing is running, and explains nothing", () => {
    const { compact, clear } = foldControls(null);
    expect(compact).toEqual({ inert: false, label: "Compact now" });
    expect(clear).toEqual({ inert: false, label: "Clear context" });
  });

  it("makes BOTH inert during a compact", () => {
    // Clear was left reachable in the first pass, so a second fold was still
    // one tap away and the server refused it: the same failure, one button to
    // the left.
    const { compact, clear } = foldControls("compact");
    expect(compact.inert).toBe(true);
    expect(clear.inert).toBe(true);
  });

  it("makes both inert during a clear", () => {
    const { compact, clear } = foldControls("clear");
    expect(compact.inert).toBe(true);
    expect(clear.inert).toBe(true);
  });

  it("words the label for the fold that is ACTUALLY running", () => {
    // It took a boolean before, so a running clear made the compact button read
    // "Compacting".
    expect(foldControls("clear").compact.label).toBe("Compact now");
    expect(foldControls("clear").clear.label).toBe("Clearing");
    expect(foldControls("compact").compact.label).toBe("Compacting");
    expect(foldControls("compact").clear.label).toBe("Clear context");
  });

  it("gives the reason on both controls, naming which fold is running", () => {
    const running = foldControls("clear");
    expect(running.compact.reason).toBe("A clear is already running in this chat.");
    expect(running.clear.reason).toBe("A clear is already running in this chat.");
    expect(foldControls("compact").compact.reason)
      .toBe("A compact is already running in this chat.");
  });

  it("says nothing when there is nothing to say", () => {
    expect(foldControls(null).compact.reason).toBeUndefined();
    expect(foldControls(null).clear.reason).toBeUndefined();
  });
});
