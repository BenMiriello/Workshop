import { describe, expect, it } from "vitest";
import { parseShortcut, pressed } from "./shortcut";

const key = (code: string, mods: Partial<Record<"ctrlKey" | "shiftKey" | "altKey" | "metaKey", boolean>> = {}) => ({
  key: code.replace(/^Key/, "").toLowerCase(), code,
  ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...mods,
});

describe("parseShortcut", () => {
  it("reads Mod as Cmd on a Mac and Ctrl elsewhere", () => {
    expect(parseShortcut("Mod+Alt+V", true)).toEqual({ code: "KeyV", ctrl: false, shift: false, alt: true, meta: true });
    expect(parseShortcut("Mod+Alt+V", false)).toEqual({ code: "KeyV", ctrl: true, shift: false, alt: true, meta: false });
  });

  it("reads named keys, digits and function keys", () => {
    expect(parseShortcut("Ctrl+Shift+Space", false)?.code).toBe("Space");
    expect(parseShortcut("Cmd+/", true)?.code).toBe("Slash");
    expect(parseShortcut("Alt+3", false)?.code).toBe("Digit3");
    expect(parseShortcut("F7", false)).toEqual({ code: "F7", ctrl: false, shift: false, alt: false, meta: false });
  });

  it("refuses a chord that would fire while typing, or is not one", () => {
    for (const bad of ["V", "Shift+V", "Mod+", "Mod++", "Hyper+V", "Mod+Mod+V", "Mod+VV", ""]) {
      expect(parseShortcut(bad, false)).toBeNull();
    }
    expect(parseShortcut(undefined, false)).toBeNull();
    expect(parseShortcut(7, false)).toBeNull();
  });
});

describe("pressed", () => {
  const chord = parseShortcut("Mod+Alt+V", false)!;
  it("matches the exact chord on its physical key", () => {
    expect(pressed(key("KeyV", { ctrlKey: true, altKey: true }), chord)).toBe(true);
    expect(pressed(key("KeyV", { ctrlKey: true, altKey: true, shiftKey: true }), chord)).toBe(false);
    expect(pressed(key("KeyB", { ctrlKey: true, altKey: true }), chord)).toBe(false);
  });
  it("ignores a held key repeating", () => {
    expect(pressed({ ...key("KeyV", { ctrlKey: true, altKey: true }), repeat: true }, chord)).toBe(false);
  });
});
