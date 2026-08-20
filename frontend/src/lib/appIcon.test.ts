import { describe, expect, it } from "vitest";
import { ICON_TOKENS, iconComponent, monogram, resolveAppIcon } from "./appIcon";

describe("monogram", () => {
  it("takes two letters, because one is ambiguous on this very instance", () => {
    // Budget and Birds both render "B" with a single letter. That is the exact
    // defect the shell shipped with on 2026-08-20.
    expect(monogram("Budget")).toBe("BU");
    expect(monogram("Birds")).toBe("BI");
  });

  it("uses initials for a multi-word name, the way a person would write it", () => {
    expect(monogram("Yard Birds")).toBe("YB");
    expect(monogram("notes-app")).toBe("NA");
    expect(monogram("open_wearables")).toBe("OW");
  });

  it("survives a one-character name and an empty one", () => {
    expect(monogram("X")).toBe("X");
    expect(monogram("")).toBe("?");
    expect(monogram("   ")).toBe("?");
  });

  it("does not cut an astral-plane character in half", () => {
    // slice(0, 2) on "🐦x" takes half a surrogate pair and renders a tofu box.
    expect(monogram("🐦x")).toBe("🐦X");
  });
});

describe("resolveAppIcon", () => {
  it("accepts a bare token name", () => {
    expect(resolveAppIcon("bird", "Yard Birds")).toEqual({ kind: "token", name: "bird" });
  });

  it("accepts the lucide export spelling the backend cards already use", () => {
    // AppSpec cards say "PiggyBank" / "TrainFront" / "NotebookPen".
    expect(resolveAppIcon("PiggyBank", "Budget")).toEqual({ kind: "token", name: "piggy-bank" });
    expect(resolveAppIcon("TrainFront", "Commute")).toEqual({ kind: "token", name: "train-front" });
  });

  it("treats a url or a path as a custom image", () => {
    expect(resolveAppIcon("https://x/y.png", "X")).toEqual({ kind: "image", src: "https://x/y.png" });
    expect(resolveAppIcon("/uploads/a.svg", "X")).toEqual({ kind: "image", src: "/uploads/a.svg" });
  });

  it("falls back to the monogram for an unknown token, never a broken glyph", () => {
    expect(resolveAppIcon("no-such-icon", "Yard Birds")).toEqual({ kind: "monogram", text: "YB" });
    expect(resolveAppIcon(null, "Budget")).toEqual({ kind: "monogram", text: "BU" });
    expect(resolveAppIcon(undefined, "Budget")).toEqual({ kind: "monogram", text: "BU" });
    expect(resolveAppIcon("", "Budget")).toEqual({ kind: "monogram", text: "BU" });
  });

  it("passes through an explicit structured declaration", () => {
    expect(resolveAppIcon({ kind: "image", src: "/a.png" }, "X")).toEqual({
      kind: "image",
      src: "/a.png",
    });
    expect(resolveAppIcon({ kind: "monogram", text: "ZZ" }, "X")).toEqual({
      kind: "monogram",
      text: "ZZ",
    });
  });

  it("rejects a structured declaration that names an icon we do not have", () => {
    expect(resolveAppIcon({ kind: "token", name: "nope" }, "Budget")).toEqual({
      kind: "monogram",
      text: "BU",
    });
  });
});

describe("the token vocabulary", () => {
  it("names no app", () => {
    // The whole point: this table is a design vocabulary, not a roster. If an
    // app id ever appears here, the platform has learned about an app again.
    const apps = ["budget", "finance", "commute", "birds", "location", "health", "wearables", "notes-app"];
    for (const token of ICON_TOKENS) expect(apps).not.toContain(token);
  });

  it("resolves every token it advertises", () => {
    for (const token of ICON_TOKENS) expect(iconComponent(token)).toBeTruthy();
  });
});
