import { describe, expect, it } from "vitest";
import { dictationKind, type FieldDesc } from "./targets";

const field = (over: Partial<FieldDesc>): FieldDesc => ({
  tag: "INPUT", type: "text", editable: false, readOnly: false, disabled: false, optedOut: false, ...over,
});

describe("dictationKind", () => {
  it("accepts text-like inputs and textareas", () => {
    for (const type of ["", "text", "search", "email", "url", "TEXT"]) {
      expect(dictationKind(field({ type }))).toBe("value");
    }
    expect(dictationKind(field({ tag: "TEXTAREA", type: undefined }))).toBe("value");
  });

  it("refuses inputs that cannot hold a spoken phrase", () => {
    for (const type of ["password", "number", "date", "datetime-local", "time", "color", "range", "file", "checkbox", "tel", "hidden"]) {
      expect(dictationKind(field({ type }))).toBeNull();
    }
  });

  it("refuses a text input that asks for a number keypad, and spin buttons", () => {
    for (const inputMode of ["numeric", "decimal", "tel", "none", "NUMERIC"]) {
      expect(dictationKind(field({ inputMode }))).toBeNull();
    }
    expect(dictationKind(field({ role: "spinbutton" }))).toBeNull();
    expect(dictationKind(field({ inputMode: "text" }))).toBe("value");
    expect(dictationKind(field({ tag: "TEXTAREA", type: undefined, inputMode: "search" }))).toBe("value");
  });

  it("accepts a contenteditable element", () => {
    expect(dictationKind(field({ tag: "DIV", type: undefined, editable: true }))).toBe("editable");
  });

  it("refuses an ordinary element", () => {
    expect(dictationKind(field({ tag: "BUTTON", type: undefined }))).toBeNull();
  });

  it("refuses read-only, disabled and opted-out fields", () => {
    expect(dictationKind(field({ readOnly: true }))).toBeNull();
    expect(dictationKind(field({ disabled: true }))).toBeNull();
    expect(dictationKind(field({ optedOut: true }))).toBeNull();
    expect(dictationKind(field({ tag: "DIV", editable: true, optedOut: true }))).toBeNull();
  });
});
