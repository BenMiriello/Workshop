import { describe, expect, it } from "vitest";
import { atRule, localInputValue, resourceText, ruleText, waitingText, whenText } from "./sendRules";

const now = new Date(2026, 9, 24, 13, 0);

describe("whenText", () => {
  it("names only the time today, the weekday this week, the date beyond", () => {
    expect(whenText(new Date(2026, 9, 24, 15, 0).toISOString(), now)).toBe("3:00 PM");
    expect(whenText(new Date(2026, 9, 26, 9, 5).toISOString(), now)).toBe("Mon 9:05 AM");
    expect(whenText(new Date(2026, 10, 3, 8, 0).toISOString(), now)).toBe("Nov 3, 8:00 AM");
  });
});

describe("ruleText", () => {
  it("says each rule plainly", () => {
    expect(ruleText({ kind: "after_turn" })).toBe("Sends after this reply");
    expect(ruleText({ kind: "hold" })).toBe("Held until you send it");
    expect(ruleText({ kind: "at", at: new Date(2026, 9, 24, 15, 0).toISOString() }, { now }))
      .toBe("Sends at 3:00 PM");
    expect(ruleText({ kind: "usage_reset", at: null })).toBe("Sends when the usage limit resets");
    expect(ruleText({ kind: "resources_free" }, { resources: ["gpu"] }))
      .toBe("Sends when the GPU is free");
    expect(ruleText({ kind: "after_thread", thread_id: "x" }, { chatTitle: () => "Renders" }))
      .toBe('Sends after "Renders" finishes');
  });

  it("never writes an em-dash", () => {
    for (const kind of ["after_turn", "at", "usage_reset", "after_thread", "resources_free", "hold"]) {
      expect(ruleText({ kind })).not.toContain("—");
    }
  });
});

describe("waitingText", () => {
  it("names the reset time when it is known", () => {
    expect(waitingText({ reason: "usage_limit", until: new Date(2026, 9, 24, 20, 10).toISOString() }, now))
      .toBe("Waiting for the usage limit to reset at 8:10 PM.");
    expect(waitingText({ reason: "usage_limit", until: null })).toBe("Waiting for the usage limit to reset.");
  });
});

describe("atRule", () => {
  it("turns a local field value into a future time and refuses the past", () => {
    const later = localInputValue(new Date(2026, 9, 24, 18, 30));
    expect(atRule(later, now)).toEqual({ kind: "at", at: new Date(2026, 9, 24, 18, 30).toISOString() });
    expect(atRule(localInputValue(new Date(2026, 9, 24, 12, 0)), now)).toBeNull();
    expect(atRule("", now)).toBeNull();
  });
});

describe("resourceText", () => {
  it("upper-cases short names", () => {
    expect(resourceText(["gpu"])).toBe("GPU");
    expect(resourceText(["gpu", "memory"])).toBe("GPU and memory");
  });
});
