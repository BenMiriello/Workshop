import { describe, expect, it } from "vitest";
import { STOPPED_BY_OPERATOR, STOPPED_INTERRUPTED, stoppedTag } from "./stopReason";

describe("stoppedTag", () => {
  it("says only that the user interrupted it", () => {
    expect(stoppedTag(STOPPED_BY_OPERATOR)).toBe("user interrupted");
  });

  it("does not blame the operator for a restart", () => {
    const tag = stoppedTag(STOPPED_INTERRUPTED);
    expect(tag).toBe("interrupted by a restart");
    expect(tag).not.toContain("user interrupted");
  });

  it("treats an unknown reason as an interruption, not as a Stop", () => {
    // Anything the backend has not taught this file about is something the
    // operator did not do. Claiming they pressed Stop when they did not is the
    // worse of the two wrong answers.
    expect(stoppedTag("something-new")).toBe(stoppedTag(STOPPED_INTERRUPTED));
  });

  it("gives the two endings different wording", () => {
    expect(stoppedTag(STOPPED_BY_OPERATOR)).not.toBe(stoppedTag(STOPPED_INTERRUPTED));
  });

  it("tells the operator nothing they already know", () => {
    // The line used to end "Send a message to continue.", said to someone who
    // had just pressed Stop. It is the app narrating itself.
    for (const r of [STOPPED_BY_OPERATOR, STOPPED_INTERRUPTED]) {
      expect(stoppedTag(r)).not.toContain("Send a message");
      expect(stoppedTag(r)).not.toContain("You stopped");
    }
  });

  it("stays short enough to be one quiet line", () => {
    for (const r of [STOPPED_BY_OPERATOR, STOPPED_INTERRUPTED, "other"]) {
      expect(stoppedTag(r).length).toBeLessThanOrEqual(30);
      expect(stoppedTag(r)).not.toContain("—");
    }
  });
});
