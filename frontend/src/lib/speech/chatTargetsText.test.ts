import { describe, expect, it } from "vitest";
import { joinAppend, restoreAttachments, restoreText, swapTail } from "./chatTargetsText";

describe("joinAppend", () => {
  it("puts a space between words", () => {
    expect(joinAppend("hello", "there")).toBe("hello there");
  });
  it("adds nothing when either side brings whitespace", () => {
    expect(joinAppend("hello ", "there")).toBe("hello there");
    expect(joinAppend("hello", "\nthere")).toBe("hello\nthere");
    expect(joinAppend("line\n", "next")).toBe("line\nnext");
  });
  it("starts an empty draft without a leading space", () => {
    expect(joinAppend("", " first")).toBe("first");
    expect(joinAppend("same", "")).toBe("same");
  });
});

describe("restoreText", () => {
  it("is the sent text when nothing was typed since", () => {
    expect(restoreText("take this back", "")).toBe("take this back");
    expect(restoreText("take this back", "  \n")).toBe("take this back");
  });
  it("puts what was typed since on the next line", () => {
    expect(restoreText("take this back", "and more")).toBe("take this back\nand more");
    expect(restoreText("sent", "\n  typed")).toBe("sent\ntyped");
  });
  it("is what was typed when nothing was sent as text", () => {
    expect(restoreText("", "typed")).toBe("typed");
  });
});

describe("restoreAttachments", () => {
  it("lists the sent files first and each file once", () => {
    const a = { id: "a" };
    const b = { id: "b" };
    const c = { id: "c" };
    expect(restoreAttachments([a, b], [b, c])).toEqual([a, b, c]);
    expect(restoreAttachments([], [c])).toEqual([c]);
  });
});

describe("swapTail: the words still being said, rewritten at a draft's end", () => {
  it("replaces the old version with the new one", () => {
    expect(swapTail("Typed. I was say", "I was say", "I was saying")).toBe("Typed. I was saying");
  });
  it("settles into the settled text and the rest, keeping a line break", () => {
    expect(swapTail("Typed. I was saying", "I was saying", "I was saying.\nAnd")).toBe("Typed. I was saying.\nAnd");
  });
  it("adds the words when the draft was edited since", () => {
    expect(swapTail("Something else", "I was say", "I was saying")).toBe("Something else I was saying");
  });
  it("takes words out when nothing is being said any more", () => {
    expect(swapTail("Typed. um", "um", "")).toBe("Typed.");
  });
});
