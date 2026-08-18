import { describe, it, expect } from "vitest";
import { scopeSwitch } from "./scopeSwitch";

const at = (over: Partial<Parameters<typeof scopeSwitch>[0]> = {}) =>
  scopeSwitch({
    target: "development",
    current: "general",
    hasHistory: true,
    turnActive: false,
    ...over,
  });

describe("changing scope moves the conversation", () => {
  it("rescopes a real conversation instead of leaving it behind", () => {
    // The whole point. This is the bug the operator reported twice: the chat
    // appeared to be wiped when he switched scope.
    expect(at().action).toBe("rescope");
  });

  it("does nothing when the scope has not changed", () => {
    expect(at({ target: "general" }).action).toBe("noop");
  });

  it("does nothing for an empty target", () => {
    expect(at({ target: "" }).action).toBe("noop");
  });

  it("just switches an empty chat, because there is nothing to move", () => {
    expect(at({ hasHistory: false }).action).toBe("switch");
  });

  it("switches an empty chat even mid-run", () => {
    // Nothing to corrupt: there is no transcript to carry across.
    expect(at({ hasHistory: false, turnActive: true }).action).toBe("switch");
  });

  it("refuses to move a conversation with a turn in flight", () => {
    // Rescope drops the live session the run is speaking through.
    const r = at({ turnActive: true });
    expect(r.action).toBe("refuse");
    expect(r.action === "refuse" && r.reason).toContain("mid-turn");
  });

  it("says why it refused, rather than ignoring the click", () => {
    const r = at({ turnActive: true });
    expect(r.action === "refuse" && r.reason.length).toBeGreaterThan(0);
  });

  it("has no em-dash in the refusal copy", () => {
    const r = at({ turnActive: true });
    expect(r.action === "refuse" && r.reason).not.toContain("—");
  });
});
