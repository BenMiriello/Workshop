import { describe, expect, it } from "vitest";
import { dropLast, putBack, sentencesOf, takeOut } from "./phraseUndo";

describe("takeOut and putBack", () => {
  it("removes the phrase where it was placed and puts it back", () => {
    const value = "Hello there. And more.";
    const p = { start: 12, text: " And more." };
    const out = takeOut(value, p)!;
    expect(out).toEqual({ value: "Hello there.", caret: 12 });
    expect(putBack(out.value, p)).toEqual({ value, caret: value.length });
  });

  it("refuses when the field no longer holds the phrase there", () => {
    expect(takeOut("Hello there. Edited.", { start: 12, text: " And more." })).toBeNull();
    expect(takeOut("abc", { start: 0, text: "" })).toBeNull();
    expect(putBack("abc", { start: 9, text: "x" })).toBeNull();
  });
});

describe("dropLast", () => {
  it("takes the last phrase off a clipboard dictation", () => {
    expect(dropLast("One. Two three.", "Two three.")).toBe("One.");
    expect(dropLast("One.", "Other.")).toBe("One.");
  });
});

describe("sentencesOf", () => {
  it("splits a placed piece into its sentences, each at its own start", () => {
    expect(sentencesOf({ start: 6, text: " One here. Two there? Three" })).toEqual([
      { start: 6, text: " One here." }, { start: 16, text: " Two there?" }, { start: 27, text: " Three" },
    ]);
    expect(sentencesOf({ start: 0, text: "Single." })).toEqual([{ start: 0, text: "Single." }]);
  });
});
