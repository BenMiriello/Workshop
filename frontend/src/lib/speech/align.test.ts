import { describe, expect, it } from "vitest";
import { alignPieces, pieceAt, tokens } from "./align";

const slice = (text: string, s: [number, number] | null) => (s ? text.slice(s[0], s[1]) : null);

describe("finding read sentences in the page text", () => {
  it("finds each sentence in order, passing over what is shown around them", () => {
    const page = "Notes / plans\nFirst point here. Copy\nSecond point, with more words.";
    const spans = alignPieces(["First point here.", "Second point, with more words."], tokens(page));
    expect(spans.map((s) => slice(page, s))).toEqual(["First point here", "Second point, with more words"]);
  });

  it("does not match a later sentence to an earlier copy of its words", () => {
    const page = "Yes it works. Then more. Yes it works.";
    const spans = alignPieces(["Then more.", "Yes it works."], tokens(page));
    expect(spans[1]![0]).toBe(page.lastIndexOf("Yes"));
  });

  it("pins a sentence the page shows differently by its first and last words", () => {
    const page = "Look at the chart below for the totals by month.";
    const spans = alignPieces(["Look at the bar chart below for the totals by month."], tokens(page));
    expect(slice(page, spans[0])).toBe("Look at the chart below for the totals by month");
  });

  it("leaves a sentence that is not on the page without a place and keeps looking", () => {
    const page = "One sentence. Two sentence.";
    const spans = alignPieces(["One sentence.", "Not here at all.", "Two sentence."], tokens(page));
    expect(spans[1]).toBeNull();
    expect(slice(page, spans[2])).toBe("Two sentence");
  });

  it("finds the sentences a page shows when it shows only some of them", () => {
    const page = "Maybe she stays late. She finds the old map in the library.";
    const read = ["Maybe she leaves early and never comes back to the school.", "Three more lines here.",
      "Maybe she stays late.", "She finds the old map in the library."];
    const spans = alignPieces(read, tokens(page));
    expect(spans.map((s) => slice(page, s))).toEqual([null, null, "Maybe she stays late", "She finds the old map in the library"]);
  });

  it("never pins a sentence to a far later one that starts with the same words", () => {
    const filler = Array.from({ length: 80 }, (_, i) => `word${i}`).join(" ");
    const page = `Intro line here. ${filler}. Maybe she runs away from home tonight. Ending words now.`;
    const read = ["Intro line here.", "Maybe she hides in the attic all winter long.", "Ending words now."];
    const spans = alignPieces(read, tokens(page));
    expect(spans[1]).toBeNull();
    expect(slice(page, spans[2])).toBe("Ending words now");
  });

  it("matches words regardless of case and curly apostrophes", () => {
    const page = "It’s Fine";
    expect(alignPieces(["it's fine"], tokens(page))[0]).toEqual([0, 9]);
  });

  it("names the sentence a tap lands in", () => {
    const spans: ([number, number] | null)[] = [[0, 10], null, [12, 30]];
    expect(pieceAt(spans, 5)).toBe(0);
    expect(pieceAt(spans, 11)).toBe(0);
    expect(pieceAt(spans, 20)).toBe(2);
    expect(pieceAt([null, [4, 8]], 1)).toBe(1);
    expect(pieceAt([null], 1)).toBeNull();
  });
});
