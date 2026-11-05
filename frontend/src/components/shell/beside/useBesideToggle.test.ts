import { describe, expect, it } from "vitest";
import { isBesideKey } from "./useBesideToggle";

const key = (code: string, over: Partial<KeyboardEvent> = {}) =>
  ({ code, ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, ...over }) as KeyboardEvent;

describe("isBesideKey", () => {
  it("is Ctrl+I on a Mac", () => {
    expect(isBesideKey(key("KeyI"), true)).toBe(true);
  });
  it("is Ctrl+` on every platform and never Ctrl+I off a Mac", () => {
    expect(isBesideKey(key("Backquote"), true)).toBe(true);
    expect(isBesideKey(key("Backquote"), false)).toBe(true);
    expect(isBesideKey(key("KeyI"), false)).toBe(false);
  });
  it("needs Ctrl alone", () => {
    expect(isBesideKey(key("KeyI", { ctrlKey: false }), true)).toBe(false);
    expect(isBesideKey(key("KeyI", { metaKey: true }), true)).toBe(false);
    expect(isBesideKey(key("KeyI", { shiftKey: true }), true)).toBe(false);
  });
});
