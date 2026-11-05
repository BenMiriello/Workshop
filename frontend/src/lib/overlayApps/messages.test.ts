import { describe, expect, it } from "vitest";
import { CONTEXT_MSG, INSERT_MAX, contextMessage, parseOverlayMessage } from "./messages";

describe("parseOverlayMessage", () => {
  it("reads the three app messages", () => {
    expect(parseOverlayMessage({ v: 1, type: "workshop/v1/hello" })).toEqual({ kind: "hello" });
    expect(parseOverlayMessage({ v: 1, type: "workshop/v1/closeOverlay" })).toEqual({ kind: "close" });
    expect(parseOverlayMessage({ v: 1, type: "workshop/v1/insertText", text: "hi" }))
      .toEqual({ kind: "insert", text: "hi" });
  });

  it("ignores anything else", () => {
    for (const bad of [null, "x", {}, { v: 2, type: "workshop/v1/hello" },
      { v: 1, type: "workshop/v1/nav" }, { v: 1, type: "workshop/v1/insertText" },
      { v: 1, type: "workshop/v1/insertText", text: "" },
      { v: 1, type: "workshop/v1/insertText", text: 5 },
      { v: 1, type: "workshop/v1/insertText", text: "x".repeat(INSERT_MAX + 1) }]) {
      expect(parseOverlayMessage(bad)).toBeNull();
    }
  });
});

describe("contextMessage", () => {
  it("names the remembered field, or says there is none", () => {
    expect(contextMessage("Message")).toEqual({ v: 1, type: CONTEXT_MSG, overlay: true, field: { name: "Message" } });
    expect(contextMessage(null)).toEqual({ v: 1, type: CONTEXT_MSG, overlay: true, field: null });
  });
});
