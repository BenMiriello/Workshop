import { describe, expect, it } from "vitest";
import { changesBetween, matches, merge3, mergeField, mergeTags, words } from "./documentMerge";

const apply = (a: string, ch: { from: number; to: number; insert: string }[]) =>
  [...ch].reverse().reduce((s, c) => s.slice(0, c.from) + c.insert + s.slice(c.to), a);

describe("merge3", () => {
  const base = "One fish.\n\nTwo fish.\n\nRed fish.\n";

  it("keeps edits to different paragraphs from both sides", () => {
    const mine = "One big fish.\n\nTwo fish.\n\nRed fish.\n";
    const theirs = "One fish.\n\nTwo fish.\n\nRed fish, blue fish.\n";
    expect(merge3(base, mine, theirs)).toEqual({ text: "One big fish.\n\nTwo fish.\n\nRed fish, blue fish.\n", conflicts: 0 });
  });

  it("keeps different words changed in the same paragraph", () => {
    expect(merge3("the quick brown fox", "the slow brown fox", "the quick brown cat"))
      .toEqual({ text: "the slow brown cat", conflicts: 0 });
  });

  it("keeps both additions at the end, theirs first", () => {
    const r = merge3(base, `${base}\nmine`, `${base}\ntheirs`);
    expect(r.conflicts).toBe(0);
    expect(r.text).toContain("theirs");
    expect(r.text).toContain("mine");
    expect(r.text.indexOf("theirs")).toBeLessThan(r.text.indexOf("mine"));
  });

  it("keeps mine where both changed the same words, and counts it", () => {
    expect(merge3("a cat sat", "a dog sat", "a cow sat")).toEqual({ text: "a dog sat", conflicts: 1 });
  });

  it("keeps theirs where both changed the same words when asked to", () => {
    expect(merge3("a cat sat", "a dog sat", "a cow sat", "theirs")).toEqual({ text: "a cow sat", conflicts: 1 });
    expect(mergeField("Old", "Mine", "Theirs", "theirs")).toEqual({ text: "Theirs", conflicts: 1 });
  });

  it("never lets an empty stale copy erase writing saved since, preferring theirs", () => {
    const later = `${base}\nBlue fish.\n`.replace("Two", "Three");
    expect(merge3(base, "", later, "theirs").text).toBe(later);
    expect(merge3(base, "", later).text).toBe("");
  });

  it("still keeps a stale copy's edits that do not overlap, preferring theirs", () => {
    const mine = "One big fish.\n\nTwo fish.\n\nRed fish.\n";
    const theirs = "One fish.\n\nTwo fish.\n\nRed fish, blue fish.\n";
    expect(merge3(base, mine, theirs, "theirs").text).toBe("One big fish.\n\nTwo fish.\n\nRed fish, blue fish.\n");
  });

  it("takes the one side that changed", () => {
    expect(merge3(base, base, "new")).toEqual({ text: "new", conflicts: 0 });
    expect(merge3(base, "new", base)).toEqual({ text: "new", conflicts: 0 });
    expect(merge3(base, "same", "same")).toEqual({ text: "same", conflicts: 0 });
  });

  it("keeps a deletion on one side and an edit elsewhere on the other", () => {
    const mine = "One fish.\n\nRed fish.\n";
    const theirs = "One fish.\n\nTwo fish.\n\nRed fish!\n";
    expect(merge3(base, mine, theirs).text).toBe("One fish.\n\nRed fish!\n");
  });

  it("merges a long note quickly", () => {
    const para = (i: number) => `Paragraph ${i} has some words in it about thing ${i}.\n\n`;
    const longBase = Array.from({ length: 1500 }, (_, i) => para(i)).join("");
    const mine = longBase.replace("thing 10.", "thing ten.");
    const theirs = `${longBase.replace("thing 1400.", "thing 1400, edited.")}The end.\n`;
    const t0 = performance.now();
    const r = merge3(longBase, mine, theirs);
    expect(performance.now() - t0).toBeLessThan(1000);
    expect(r.conflicts).toBe(0);
    expect(r.text).toContain("thing ten.");
    expect(r.text).toContain("thing 1400, edited.");
    expect(r.text.endsWith("The end.\n")).toBe(true);
  });
});

describe("matches", () => {
  it("pairs every token of a common subsequence in order", () => {
    const a = words("a b c d e");
    const b = words("a x c d y e");
    const m = matches(a, b)!;
    const pairs = [...m].map((j, i) => [i, j]).filter(([, j]) => j >= 0);
    for (const [i, j] of pairs) expect(a[i]).toBe(b[j]);
    for (let k = 1; k < pairs.length; k++) expect(pairs[k][1]).toBeGreaterThan(pairs[k - 1][1]);
    expect(pairs.length).toBe(8);
  });

  it("gives up past the limit", () => {
    expect(matches(words("a b c d"), words("w x y z"), 2)).toBeNull();
  });
});

describe("mergeField and mergeTags", () => {
  it("takes the side that changed a title, mine when both did", () => {
    expect(mergeField("A", "A", "B")).toEqual({ text: "B", conflicts: 0 });
    expect(mergeField("A", "C", "A")).toEqual({ text: "C", conflicts: 0 });
    expect(mergeField("A", "C", "B")).toEqual({ text: "C", conflicts: 1 });
  });

  it("adds and removes tags from both sides", () => {
    expect(mergeTags(["a", "b"], ["a", "b", "m"], ["a", "t"])).toEqual(["a", "t", "m"]);
    expect(mergeTags(["a", "b"], ["b"], ["a", "b", "t"])).toEqual(["b", "t"]);
  });
});

describe("changesBetween", () => {
  it("turns one text into the other with edits that leave the rest alone", () => {
    const a = "Top line.\n\nMiddle where I type.\n\nBottom line.";
    const b = "Top line, edited.\n\nMiddle where I type.\n\nBottom line. More.";
    const ch = changesBetween(a, b);
    expect(apply(a, ch)).toBe(b);
    const typing = a.indexOf("where");
    expect(ch.every((c) => c.to <= typing || c.from > typing + 5)).toBe(true);
  });

  it("returns nothing for the same text", () => {
    expect(changesBetween("x y", "x y")).toEqual([]);
  });
});
