import { describe, expect, it } from "vitest";
import { speakableText, splitSentences } from "./sentences";

describe("splitSentences", () => {
  it("splits on sentence ends", () => {
    expect(splitSentences("The first one is here. Is this the second one? Yes it is the third!"))
      .toEqual(["The first one is here.", "Is this the second one?", "Yes it is the third!"]);
  });

  it("keeps a closing quote or bracket with its sentence", () => {
    expect(splitSentences("She said \"go home.\" Then she left the room."))
      .toEqual(["She said \"go home.\"", "Then she left the room."]);
  });

  it("does not split after an abbreviation, an initial, or inside a number", () => {
    expect(splitSentences("Talk to Dr. Smith about it, e.g. tomorrow. Version 3.14 is out now."))
      .toEqual(["Talk to Dr. Smith about it, e.g. tomorrow.", "Version 3.14 is out now."]);
    expect(splitSentences("Written by J. R. Tolkien in England. Read it soon."))
      .toEqual(["Written by J. R. Tolkien in England.", "Read it soon."]);
  });

  it("treats a line break as a boundary", () => {
    expect(splitSentences("A heading line here\nThen a paragraph follows."))
      .toEqual(["A heading line here", "Then a paragraph follows."]);
  });

  it("folds a very short fragment into the next sentence", () => {
    expect(splitSentences("Okay. That should work fine for now."))
      .toEqual(["Okay. That should work fine for now."]);
  });

  it("folds a short trailing fragment into the previous sentence", () => {
    expect(splitSentences("That should work fine for now. Okay."))
      .toEqual(["That should work fine for now. Okay."]);
  });

  it("cuts a run-on at a clause boundary within the limit", () => {
    const long = "one two three four five, six seven eight nine ten, eleven twelve thirteen";
    const pieces = splitSentences(long, 40);
    expect(pieces.every((p) => p.length <= 40)).toBe(true);
    expect(pieces[0]).toBe("one two three four five,");
    expect(pieces.join(" ")).toBe(long);
  });

  it("cuts at a word when there is no clause boundary", () => {
    const pieces = splitSentences("alpha beta gamma delta epsilon zeta eta theta iota kappa", 20);
    expect(pieces.every((p) => p.length <= 20)).toBe(true);
    expect(pieces.join(" ")).toBe("alpha beta gamma delta epsilon zeta eta theta iota kappa");
  });

  it("returns nothing for blank text", () => {
    expect(splitSentences("  \n \n")).toEqual([]);
  });
});

describe("speakableText", () => {
  it("drops code fences and keeps inline code words", () => {
    expect(speakableText("Run `npm test` now.\n```sh\nnpm test\n```\nDone.")).toBe("Run npm test now.\n\nDone.");
  });

  it("keeps link and image text, not their targets", () => {
    expect(speakableText("See [the docs](https://x.test/a) and ![a chart](c.png)."))
      .toBe("See the docs and a chart.");
  });

  it("strips heading, quote and list markers", () => {
    expect(speakableText("## Title\n> quoted\n- item one\n2. item two"))
      .toBe("Title\nquoted\nitem one\nitem two");
  });

  it("strips emphasis but not underscores inside words", () => {
    expect(speakableText("This is **bold**, *soft* and _light_ in snake_case_name."))
      .toBe("This is bold, soft and light in snake_case_name.");
  });

  it("drops table rules and pipes", () => {
    expect(speakableText("| a | b |\n|---|---|\n| 1 | 2 |")).toBe("a b\n\n1 2");
  });

  it("reads comparisons and generics instead of taking them for tags", () => {
    expect(speakableText("If x < 3 and y > 2 then stop.")).toBe("If x < 3 and y > 2 then stop.");
    expect(speakableText("It returns List<String> and a<b and c>d.")).toBe("It returns List<String> and a<b and c>d.");
  });

  it("keeps inline code whole, angle brackets and underscores included", () => {
    expect(speakableText("Use `Promise<void>` here.")).toBe("Use Promise<void> here.");
    expect(speakableText("Call `load_all_<T>` and *then* `a > b`.")).toBe("Call load_all_<T> and then a > b.");
  });

  it("does not let a fence pair markup with text outside it", () => {
    expect(speakableText("Before <\n```\n<div>\n```\nafter > done.")).toBe("Before <\n\nafter > done.");
  });

  it("strips real tags, comments and autolinks", () => {
    expect(speakableText("<p>Some <b>bold</b> text<br/> and <a href=\"x\">a link</a>.</p>"))
      .toBe("Some bold text and a link .");
    expect(speakableText("Hidden <!-- note --> words, see <https://x.test/a>.")).toBe("Hidden words, see .");
  });
});
