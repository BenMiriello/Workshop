import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../../lib/chat";
import { hasBubble, noReplyText } from "./replyBody";

const reply = (over: Partial<ChatMsg> = {}): ChatMsg => ({
  role: "assistant", content: "", ts: "2026-08-10T12:00:00Z", ...over,
});

describe("hasBubble", () => {
  it("draws NO bubble for a turn stopped before it wrote anything", () => {
    // The operator's report: "when I just hit stop it shows a blank system chat
    // bubble then user interrupted". An empty box above the line reads as a
    // rendering fault; the line alone is the whole treatment.
    expect(hasBubble(reply({ meta: { stopped: "operator" } }))).toBe(false);
  });

  it("keeps the bubble when a stopped turn HAD written something", () => {
    expect(hasBubble(reply({ content: "got partway", meta: { stopped: "operator" } }))).toBe(true);
  });

  it("keeps the bubble for a stopped turn that ran tools", () => {
    // The work it got through is the honest record of the turn, even with no
    // closing text.
    expect(hasBubble(reply({
      meta: { stopped: "operator", segments: [{ t: "tool", name: "Read", input: "a" }] },
    }))).toBe(true);
  });

  it("keeps the bubble for an error with no text", () => {
    // An error is still something that happened, and it has its own wording.
    expect(hasBubble(reply({ meta: { error: true } }))).toBe(true);
  });

  it("keeps the bubble for a timeout, which carries its own tag", () => {
    expect(hasBubble(reply({ meta: { timeout: true } }))).toBe(true);
  });

  it("keeps the bubble for an unexplained empty reply", () => {
    // This one has a sentence to show: "No response. The turn ended without
    // sending a message." Hiding it would lose the only report of the failure.
    expect(hasBubble(reply())).toBe(true);
  });

  it("always draws the operator's own messages", () => {
    expect(hasBubble({ role: "user", content: "", ts: "" })).toBe(true);
  });
});

describe("noReplyText", () => {
  it("says nothing for a stopped turn, because the line under it already does", () => {
    expect(noReplyText(reply({ meta: { stopped: "operator" } }))).toBe("");
  });

  it("answers the question a timeout tag leaves open", () => {
    expect(noReplyText(reply({ meta: { timeout: true } }))).toContain("No reply was written");
  });

  it("names the genuinely unexplained case rather than showing an empty box", () => {
    expect(noReplyText(reply())).toContain("No response");
  });

  it("uses no em-dashes", () => {
    for (const m of [reply(), reply({ meta: { timeout: true } })]) {
      expect(noReplyText(m)).not.toContain("—");
    }
  });
});
