import { describe, expect, it } from "vitest";
import { MAX_COPIED_TEXT, copiedReport, fieldSelection } from "./copyReport";

describe("copiedReport", () => {
  it("carries the text and the page it was copied on", () => {
    expect(copiedReport("hello", "/chat/1")).toEqual({ text: "hello", source: { path: "/chat/1" } });
  });
  it("names the app a copy outside Workshop was made in", () => {
    expect(copiedReport("hi", "/settings", "Safari")).toEqual({
      text: "hi", source: { path: "/settings", app: "Safari" },
    });
    expect(copiedReport("hi", "/", "")).toEqual({ text: "hi", source: { path: "/" } });
  });
  it("reports nothing for an empty or blank copy", () => {
    expect(copiedReport("", "/")).toBeNull();
    expect(copiedReport(" \n\t", "/")).toBeNull();
  });
  it("keeps surrounding whitespace of a real copy", () => {
    expect(copiedReport("  x  ", "/")?.text).toBe("  x  ");
  });
  it("cuts a copy longer than the backend accepts", () => {
    expect(copiedReport("a".repeat(MAX_COPIED_TEXT + 5), "/")?.text).toHaveLength(MAX_COPIED_TEXT);
  });
});

describe("fieldSelection", () => {
  it("reads the selected span", () => {
    expect(fieldSelection("abcdef", 1, 4)).toBe("bcd");
  });
  it("gives nothing for a collapsed selection or a field without one", () => {
    expect(fieldSelection("abc", 2, 2)).toBe("");
    expect(fieldSelection("abc", null, null)).toBe("");
  });
});
