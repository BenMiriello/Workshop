import { describe, expect, it } from "vitest";
import { appendPiece, dictationJoin, joinSaid, spliceInsert } from "./insertText";

describe("dictationJoin", () => {
  it("adds a space after a word and before a word", () => {
    expect(dictationJoin("one", "two", "middle")).toBe(" middle ");
  });

  it("adds nothing where whitespace is already there", () => {
    expect(dictationJoin("one ", " two", "middle")).toBe("middle");
  });

  it("adds nothing at the edges of an empty field", () => {
    expect(dictationJoin("", "", "Hello there.")).toBe("Hello there.");
  });

  it("does not space before punctuation the phrase starts with", () => {
    expect(dictationJoin("word", "", ", and more")).toBe(", and more");
  });

  it("does not space before closing punctuation that follows", () => {
    expect(dictationJoin("say ", ".", "hello")).toBe("hello");
  });

  it("does not space after an opening bracket or quote", () => {
    expect(dictationJoin("(", ")", "aside")).toBe("aside");
    expect(dictationJoin("\"", "", "quoted")).toBe("quoted");
  });

  it("trims the transcript and returns nothing for silence", () => {
    expect(dictationJoin("a", "b", "   ")).toBe("");
    expect(dictationJoin("", "", "  hi  ")).toBe("hi");
  });
});

describe("spliceInsert", () => {
  it("inserts at a collapsed caret", () => {
    expect(spliceInsert("hello world", 5, 5, "big")).toEqual({ value: "hello big world", caret: 9, piece: " big" });
  });

  it("replaces the selection", () => {
    expect(spliceInsert("hello world", 6, 11, "there")).toMatchObject({ value: "hello there", caret: 11 });
  });

  it("appends at the end", () => {
    expect(spliceInsert("note:", 5, 5, "buy milk")).toMatchObject({ value: "note: buy milk", caret: 14 });
  });

  it("orders a reversed selection and clamps a stale one", () => {
    expect(spliceInsert("abc", 3, 1, "x").value).toBe("a x");
    expect(spliceInsert("abc", 10, 20, "x")).toMatchObject({ value: "abc x", caret: 5 });
  });

  it("leaves the value alone when nothing was heard", () => {
    expect(spliceInsert("keep", 2, 2, "")).toEqual({ value: "keep", caret: 2, piece: "" });
  });

  it("spaces after what a self-clearing field was last sent, and only when it holds nothing before the caret", () => {
    expect(spliceInsert("", 0, 0, "now", "git status").piece).toBe(" now");
    expect(spliceInsert("", 0, 0, "now").piece).toBe("now");
    expect(spliceInsert("ls", 2, 2, "now", "git status.").piece).toBe(" now");
    expect(spliceInsert("(", 1, 1, "now", "git status").piece).toBe("now");
  });
});

describe("appendPiece", () => {
  it("joins settled phrases with one space", () => {
    expect(appendPiece("", " Hello. ")).toBe("Hello.");
    expect(appendPiece("Hello. ", "Again.")).toBe("Hello. Again.");
    expect(appendPiece("Hello.", "  ")).toBe("Hello.");
  });
});

describe("line breaks from new line and new paragraph", () => {
  it("keeps a break at the end of what has been said so far", () => {
    expect(dictationJoin("", "", "I was just saying\n")).toBe("I was just saying\n");
    expect(dictationJoin("", "", "Saying.\n\n")).toBe("Saying.\n\n");
  });

  it("adds no space against a break", () => {
    expect(dictationJoin("Before", "", "\nAfter")).toBe("\nAfter");
    expect(dictationJoin("", "next", "Line\n")).toBe("Line\n");
  });

  it("joins a settled stretch and the next without losing a break between them", () => {
    expect(joinSaid("Saying.\n", "And more.")).toBe("Saying.\nAnd more.");
    expect(joinSaid("Saying.", "\n\nAnd more.")).toBe("Saying.\n\nAnd more.");
    expect(joinSaid("One.", "Two.")).toBe("One. Two.");
    expect(appendPiece("Held words.\n", "More.")).toBe("Held words.\nMore.");
  });
});
