import { describe, expect, it } from "vitest";
import { placementFor, problemBlock } from "./chatProblem";

// The bug this pins: the chat said errors three ways, and which one you got
// depended on the code path rather than on what had failed. A send that never
// left the browser and a poll that stopped landing are different problems with
// different remedies, and they were both liable to end up as the same anonymous
// red line above the composer — where the next error overwrote it.

describe("placementFor", () => {
  it("puts a failure that names a message under that message", () => {
    expect(placementFor({ messageId: "c-1", text: "500 the turn would not start" }))
      .toBe("in-log");
  });

  it("puts a failure that names no message in the chat block", () => {
    expect(placementFor({ messageId: null, text: "Couldn't load this conversation" }))
      .toBe("chat-block");
  });

  it("treats a missing message id the same as an absent one", () => {
    expect(placementFor({ text: "Couldn't refresh the list of conversations" }))
      .toBe("chat-block");
  });

  // The one case worth a rule. A blocked network is recorded on the reply that
  // died of it, but the next message dies the same way and the remedy is not
  // "send that message again" — so it is the chat's problem, not the message's.
  it("escalates a blocked-egress failure off its message", () => {
    expect(placementFor({ messageId: "c-2", text: "fatal: unable to access https://github.com" }))
      .toBe("chat-block");
    expect(placementFor({ messageId: "c-3", text: "npm ERR! network Connection refused" }))
      .toBe("chat-block");
  });

  it("does not escalate an ordinary message failure", () => {
    expect(placementFor({ messageId: "c-4", text: "Not sent: the server refused this message (422)." }))
      .toBe("in-log");
  });
});

describe("problemBlock", () => {
  it("splits at the writer's colon", () => {
    expect(problemBlock({
      text: "Couldn't refresh the list of conversations: TypeError: Failed to fetch. The list may be out of date.",
    })).toEqual({
      title: "Couldn't refresh the list of conversations.",
      detail: "TypeError: Failed to fetch. The list may be out of date.",
    });
  });

  it("splits at the first sentence when there is no colon", () => {
    expect(problemBlock({
      text: "Not hearing from the server. This chat stopped updating 3 minutes ago.",
    })).toEqual({
      title: "Not hearing from the server.",
      detail: "This chat stopped updating 3 minutes ago.",
    });
  });

  it("keeps a single short sentence whole", () => {
    expect(problemBlock({ text: "Voice unavailable." }))
      .toEqual({ title: "Voice unavailable.", detail: null });
  });

  it("never leaves an empty title", () => {
    expect(problemBlock({ text: "" }).title).toBe("Something went wrong in this chat.");
    expect(problemBlock({ text: null }).detail).toBe(null);
  });

  // A gateway's HTML error page is the reason there is a bound at all.
  it("bounds a pathological body and says it cut", () => {
    const huge = "x".repeat(900);
    const { title, detail } = problemBlock({ text: huge });
    expect(title).toBe("Something went wrong in this chat.");
    expect(detail).toHaveLength(400);
    expect(detail?.endsWith("…")).toBe(true);
  });

  it("does not split on a colon that is buried past the title bound", () => {
    const lead = `${"a".repeat(120)}: cause`;
    expect(problemBlock({ text: lead }).title).toBe("Something went wrong in this chat.");
  });
});
