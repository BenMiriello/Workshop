import { describe, it, expect } from "vitest";
import { spliceDictation, dictationLost, typedPortion } from "./voiceDictation";

describe("dictation in the composer", () => {
  it("puts the first words in an empty box", () => {
    expect(spliceDictation("", "", "remind me")).toBe("remind me");
  });

  it("revises in place instead of appending", () => {
    // The whole flicker/duplication class comes from a revision being treated
    // as new text. A longer hypothesis REPLACES the shorter one.
    let box = spliceDictation("", "", "remind me");
    box = spliceDictation(box, "remind me", "remind me to call");
    box = spliceDictation(box, "remind me to call", "remind me to call the dentist");
    expect(box).toBe("remind me to call the dentist");
  });

  it("shrinks when the engine revises downward", () => {
    expect(spliceDictation("call the dentist", "call the dentist", "call the dentist's"))
      .toBe("call the dentist's");
  });

  it("never eats what the operator typed", () => {
    const box = spliceDictation("hello there", "hello", "hello world");
    expect(box).toBe("hello world there");
  });

  it("leaves the typed remainder behind when the utterance commits", () => {
    // Commit = the dictation became a real message. What was typed alongside it
    // is not part of that message and stays in the box.
    expect(spliceDictation("spoken words typed words", "spoken words", ""))
      .toBe("typed words");
  });

  it("empties the box when nothing was typed alongside", () => {
    expect(spliceDictation("spoken words", "spoken words", "")).toBe("");
  });

  it("puts new dictation in front of text already typed", () => {
    expect(spliceDictation("typed first", "", "then spoke")).toBe("then spoke typed first");
  });

  it("stops writing once the operator edits the dictated text", () => {
    // He rewrote it. Those are his words now; voice must not clobber them.
    const edited = "remind me to CALL";
    expect(spliceDictation(edited, "remind me to call", "remind me to call the dentist"))
      .toBe(edited);
    expect(dictationLost(edited, "remind me to call")).toBe(true);
  });

  it("does not consider an untouched prefix lost", () => {
    expect(dictationLost("remind me to call now", "remind me to call")).toBe(false);
    expect(dictationLost("anything", "")).toBe(false);
  });
});

describe("what counts as typing", () => {
  it("does not read dictation as typing", () => {
    // If it did, the engine would defer its turn waiting for the composer to
    // empty, and the composer only empties when the turn is taken.
    expect(typedPortion("remind me to call", "remind me to call")).toBe("");
  });

  it("sees the part typed after the dictation", () => {
    expect(typedPortion("remind me to call and this", "remind me to call")).toBe("and this");
  });

  it("treats everything as typed when voice owns nothing", () => {
    expect(typedPortion("just typing", "")).toBe("just typing");
  });
});
