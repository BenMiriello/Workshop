import { describe, expect, it } from "vitest";
import { capLines, extrasOf, formatLine, matchLines, nameOf, roleOf, type Node } from "./outline";

const n = (tag: string, attrs: Record<string, string> = {}, text = ""): Node => ({ tag, attrs, text });

describe("outline", () => {
  it("names roles from tags and attributes", () => {
    expect(roleOf(n("a", { href: "/x" }))).toBe("link");
    expect(roleOf(n("a"))).toBe("");
    expect(roleOf(n("input", { type: "checkbox" }))).toBe("checkbox");
    expect(roleOf(n("input"))).toBe("textbox");
    expect(roleOf(n("input", { type: "hidden" }))).toBe("");
    expect(roleOf(n("div", { role: "tab" }))).toBe("tab");
    expect(roleOf(n("div", { role: "presentation" }))).toBe("");
    expect(roleOf(n("div", { tabindex: "0" }))).toBe("button");
    expect(roleOf(n("h2"))).toBe("heading");
    expect(roleOf(n("img"))).toBe("");
  });

  it("prefers an aria label to the text", () => {
    expect(nameOf(n("button", { "aria-label": "Close" }, "x"))).toBe("Close");
    expect(nameOf(n("button", {}, "  Save   now "))).toBe("Save now");
    expect(nameOf(n("input", { type: "submit", value: "Go" }))).toBe("Go");
  });

  it("formats lines with refs, state and fresh marks", () => {
    const line = formatLine({ depth: 1, role: "button", name: 'Say "hi"', ref: "e3", extra: " [disabled]" }, true);
    expect(line).toBe("  *button \"Say 'hi'\" [ref=e3] [disabled]");
    expect(extrasOf(n("a", { href: "https://h.test/docs" }), "link", "", "https://h.test")).toBe(" -> /docs");
    expect(extrasOf(n("input", { type: "password" }), "textbox", "secret", "")).toBe(' = "••••"');
  });

  it("caps long readings and says what was left", () => {
    const out = capLines(Array.from({ length: 100 }, (_, i) => `line ${i}`), 50);
    expect(out).toContain("more lines not shown");
    expect(capLines([])).toBe("(the page shows nothing to read)");
  });

  it("finds lines containing every word", () => {
    expect(matchLines(['button "Save draft"', 'link "Drafts"', 'button "Send"'], "save draft")).toEqual(['button "Save draft"']);
    expect(matchLines(["x"], "  ")).toEqual([]);
  });
});
