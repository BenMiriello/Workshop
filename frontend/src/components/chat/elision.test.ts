import { describe, expect, it } from "vitest";
import { elidePlan, visualLineCount, CPL } from "./elision";
import { previewSplit, modelDisplay } from "./models";

const cfg = modelDisplay(null);
const linesOf = (s: string) => s.split("\n").length;

describe("previewSplit", () => {
  it("splits an even total exactly in half", () => {
    expect(previewSplit(10)).toEqual({ head: 5, tail: 5 });
    expect(previewSplit(4)).toEqual({ head: 2, tail: 2 });
  });

  it("gives an odd total's extra line to the head", () => {
    expect(previewSplit(9)).toEqual({ head: 5, tail: 4 });
  });

  it("always accounts for every previewed line", () => {
    for (let n = 2; n <= 40; n++) {
      const { head, tail } = previewSplit(n);
      expect(head + tail).toBe(n);
      // The whole point: the two halves never differ by more than the odd line.
      expect(Math.abs(head - tail)).toBeLessThanOrEqual(1);
    }
  });
});

describe("elidePlan", () => {
  it("leaves a short message alone", () => {
    expect(elidePlan("one\ntwo\nthree", cfg)).toEqual({ elide: false });
  });

  it("does not elide right at the threshold", () => {
    const src = Array.from({ length: cfg.elideOverLines }, (_, i) => `line ${i}`).join("\n");
    expect(elidePlan(src, cfg).elide).toBe(false);
  });

  it("keeps an EVEN amount above and below the cut", () => {
    const src = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const plan = elidePlan(src, cfg);
    if (!plan.elide) throw new Error("expected elision");
    expect(linesOf(plan.headSrc)).toBe(linesOf(plan.tailSrc));
  });

  it("reports the hidden count that the head and tail actually leave out", () => {
    const total = 40;
    const src = Array.from({ length: total }, (_, i) => `line ${i}`).join("\n");
    const plan = elidePlan(src, cfg);
    if (!plan.elide) throw new Error("expected elision");
    expect(plan.hidden).toBe(total - linesOf(plan.headSrc) - linesOf(plan.tailSrc));
  });

  it("previews the real start and the real end, in order", () => {
    const src = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const plan = elidePlan(src, cfg);
    if (!plan.elide) throw new Error("expected elision");
    expect(plan.headSrc.startsWith("line 0")).toBe(true);
    expect(plan.tailSrc.endsWith("line 39")).toBe(true);
  });

  it("still trims a few very long wrapped lines, which have no newlines to cut on", () => {
    const src = "x".repeat(CPL * 30);
    const plan = elidePlan(src, cfg);
    if (!plan.elide) throw new Error("expected elision");
    expect(plan.headSrc.length).toBeLessThan(src.length);
    expect(plan.tailSrc.length).toBeLessThan(src.length);
    expect(plan.hidden).toBeGreaterThan(0);
  });

  it("never reports a non-positive hidden count", () => {
    // Sweep sizes around the threshold; a "0 lines hidden" divider would be a lie.
    for (let n = 1; n <= 60; n++) {
      const plan = elidePlan(Array.from({ length: n }, (_, i) => `l${i}`).join("\n"), cfg);
      if (plan.elide) expect(plan.hidden).toBeGreaterThan(0);
    }
  });

  it("counts a wrapped paragraph as more than one visual line", () => {
    expect(visualLineCount("y".repeat(CPL * 3))).toBe(3);
    expect(visualLineCount("short")).toBe(1);
  });
});
