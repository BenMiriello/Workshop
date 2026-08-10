import { describe, expect, it } from "vitest";
import {
  STOPPED_BY_OPERATOR,
  STOPPED_INTERRUPTED,
  stoppedNoReplyText,
  stoppedTag,
} from "./stopReason";

describe("stoppedTag", () => {
  it("says the operator stopped it when they did", () => {
    expect(stoppedTag(STOPPED_BY_OPERATOR)).toBe(
      "You stopped this turn. Send a message to continue.",
    );
  });

  it("does not blame the operator for a restart", () => {
    const tag = stoppedTag(STOPPED_INTERRUPTED);
    expect(tag).toBe("Turn interrupted. Send a message to continue.");
    expect(tag).not.toContain("You stopped");
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

  it("says what to do next in both cases", () => {
    for (const r of [STOPPED_BY_OPERATOR, STOPPED_INTERRUPTED]) {
      expect(stoppedTag(r)).toContain("Send a message to continue.");
    }
  });
});

describe("stoppedNoReplyText", () => {
  it("answers the question the tag leaves open", () => {
    expect(stoppedNoReplyText(STOPPED_BY_OPERATOR)).toBe(
      "No reply was written before you stopped it.",
    );
    expect(stoppedNoReplyText(STOPPED_INTERRUPTED)).toBe(
      "No reply was written before the turn was interrupted.",
    );
  });

  it("never renders an em-dash", () => {
    for (const r of [STOPPED_BY_OPERATOR, STOPPED_INTERRUPTED, "other"]) {
      expect(stoppedTag(r)).not.toContain("—");
      expect(stoppedNoReplyText(r)).not.toContain("—");
    }
  });
});
