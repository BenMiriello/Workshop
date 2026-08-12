import { describe, expect, it } from "vitest";
import { receiptHref, receiptItems, receiptLabel } from "./receipts";

describe("receiptItems", () => {
  it("prefers typed receipts", () => {
    const receipts = [{ kind: "note", label: "Saved a note to workshop." }];
    expect(receiptItems({ receipts, notes: ["ignored"] })).toEqual(receipts);
  });

  it("reads the pre-typing form rather than losing it", () => {
    expect(receiptItems({ notes: ["Saved a note to workshop."] }))
      .toEqual([{ kind: "note", label: "Saved a note to workshop." }]);
  });

  it("keeps a stored refusal a warning", () => {
    // Without the parenthesis rule this reads back as though it succeeded.
    expect(receiptItems({ notes: ["(a triager agent may not file tickets)"] }))
      .toEqual([{ kind: "warn", label: "(a triager agent may not file tickets)" }]);
  });

  it("is empty when the turn changed nothing", () => {
    expect(receiptItems(undefined)).toEqual([]);
    expect(receiptItems({})).toEqual([]);
  });
});

describe("receiptHref", () => {
  it("opens the exact note when the write recorded which one it made", () => {
    expect(receiptHref({
      kind: "note", label: "Saved a note to workshop.",
      ref: { scope: "workshop", id: "ports-are-squatted-abc" },
    })).toBe("/memory?scope=workshop&note=ports-are-squatted-abc");
  });

  it("escapes a scope that contains a colon", () => {
    expect(receiptHref({
      kind: "note", label: "x", ref: { scope: "project:birds", id: "n1" },
    })).toBe("/memory?scope=project%3Abirds&note=n1");
  });

  it("falls back to the scope's list for a receipt stored before refs existed", () => {
    // Every note this system ever wrote has one of these. Without the fallback
    // they all stay dead sentences.
    expect(receiptHref({ kind: "note", label: "Saved a note to workshop." }))
      .toBe("/memory?scope=workshop");
    expect(receiptHref({ kind: "note", label: "Saved a note to project:birds." }))
      .toBe("/memory?scope=project%3Abirds");
  });

  it("never links a warning, even one carrying a ref", () => {
    expect(receiptHref({
      kind: "warn", label: "(note skipped: bad scope)",
      ref: { scope: "workshop", id: "n1" },
    })).toBeNull();
  });

  it("does not link a kind with nowhere to go yet", () => {
    expect(receiptHref({ kind: "ticket", label: "Filed a ticket." })).toBeNull();
    expect(receiptHref({ kind: "learning", label: "Captured a preference." })).toBeNull();
  });

  it("does not invent a link from an unrecognised note label", () => {
    expect(receiptHref({ kind: "note", label: "Wrote something somewhere" })).toBeNull();
  });
});

describe("receiptLabel", () => {
  it("shows the note's title over the bookkeeping sentence", () => {
    expect(receiptLabel({
      kind: "note", label: "Saved a note to workshop.",
      ref: { scope: "workshop", id: "n1", title: "Ports are squatted" },
    })).toBe("Ports are squatted");
  });

  it("falls back to the label when there is no title", () => {
    expect(receiptLabel({ kind: "note", label: "Saved a note to workshop." }))
      .toBe("Saved a note to workshop.");
  });
});
