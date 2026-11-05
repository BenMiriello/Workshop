import { describe, expect, it } from "vitest";
import {
  chordFromEvent, chordKeys, chordLabel, DEFAULT_CHORDS, parseChord, speechShortcut, type Chord, type KeyLike,
} from "./shortcuts";

const key = (over: Partial<KeyLike>): KeyLike => ({
  key: "", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...over,
});
const run = (over: Partial<KeyLike>, dictating = false, chords = DEFAULT_CHORDS) =>
  speechShortcut(key(over), dictating, chords);

describe("speechShortcut", () => {
  it("maps Ctrl+Shift+Space to dictation", () => {
    expect(run({ key: " ", code: "Space", ctrlKey: true, shiftKey: true })).toBe("dictate");
  });

  it("maps Ctrl+Shift+L to read aloud, whatever case the key reports", () => {
    expect(run({ key: "L", code: "KeyL", ctrlKey: true, shiftKey: true })).toBe("read");
    expect(run({ key: "l", ctrlKey: true, shiftKey: true })).toBe("read");
  });

  it("matches by physical key when the layout reports another character", () => {
    expect(run({ key: "Unidentified", code: "KeyL", ctrlKey: true, shiftKey: true })).toBe("read");
  });

  it("ignores the chords with a modifier missing or extra", () => {
    expect(run({ key: " ", code: "Space", ctrlKey: true })).toBeNull();
    expect(run({ key: " ", code: "Space", shiftKey: true })).toBeNull();
    expect(run({ key: " ", code: "Space", ctrlKey: true, shiftKey: true, metaKey: true })).toBeNull();
    expect(run({ key: "l", ctrlKey: true, shiftKey: true, altKey: true })).toBeNull();
  });

  it("ignores auto-repeat so a held chord toggles once", () => {
    expect(run({ key: " ", code: "Space", ctrlKey: true, shiftKey: true, repeat: true })).toBeNull();
  });

  it("takes Esc only while dictating, and only bare", () => {
    expect(run({ key: "Escape" }, true)).toBe("stop");
    expect(run({ key: "Escape" }, false)).toBeNull();
    expect(run({ key: "Escape", shiftKey: true }, true)).toBeNull();
  });

  it("leaves ordinary typing alone", () => {
    expect(run({ key: "l" })).toBeNull();
    expect(run({ key: " ", code: "Space" }, true)).toBeNull();
  });

  it("follows the chords it is given, not the defaults", () => {
    const chords = {
      dictate: { code: "KeyD", ctrl: false, shift: false, alt: true, meta: false },
      read: { code: "F8", ctrl: false, shift: false, alt: false, meta: false },
    };
    expect(run({ key: "∂", code: "KeyD", altKey: true }, false, chords)).toBe("dictate");
    expect(run({ key: "F8", code: "F8" }, false, chords)).toBe("read");
    expect(run({ key: " ", code: "Space", ctrlKey: true, shiftKey: true }, false, chords)).toBeNull();
  });
});

describe("chordFromEvent", () => {
  it("waits while only modifiers are down", () => {
    expect(chordFromEvent(key({ key: "Control", code: "ControlLeft", ctrlKey: true }))).toBeNull();
    expect(chordFromEvent(key({ key: "Shift", code: "ShiftLeft", ctrlKey: true, shiftKey: true }))).toBeNull();
  });

  it("records a key with its modifiers", () => {
    expect(chordFromEvent(key({ key: "K", code: "KeyK", metaKey: true, shiftKey: true })))
      .toEqual({ chord: { code: "KeyK", ctrl: false, shift: true, alt: false, meta: true } });
  });

  it("refuses a bare or shifted key, which typing would set off, but takes a function key", () => {
    expect(chordFromEvent(key({ key: "k", code: "KeyK" }))).toEqual({ refused: true });
    expect(chordFromEvent(key({ key: "K", code: "KeyK", shiftKey: true }))).toEqual({ refused: true });
    expect(chordFromEvent(key({ key: "F9", code: "F9" }))).toEqual({
      chord: { code: "F9", ctrl: false, shift: false, alt: false, meta: false },
    });
  });
});

describe("parseChord", () => {
  it("takes a whole chord and refuses anything partial", () => {
    expect(parseChord(DEFAULT_CHORDS.read)).toEqual(DEFAULT_CHORDS.read);
    expect(parseChord({ code: "KeyL", ctrl: true })).toBeNull();
    expect(parseChord("Ctrl+L")).toBeNull();
    expect(parseChord(null)).toBeNull();
  });
});

describe("chordLabel", () => {
  const all: Chord = { code: "Digit1", ctrl: true, shift: true, alt: true, meta: true };
  it("names modifiers the platform's way, in a fixed order", () => {
    expect(chordKeys(all, false)).toEqual(["Ctrl", "Alt", "Shift", "Win", "1"]);
    expect(chordKeys(all, true)).toEqual(["Control", "Option", "Shift", "Cmd", "1"]);
  });

  it("writes the defaults as they read today", () => {
    expect(chordLabel(DEFAULT_CHORDS.dictate, false)).toBe("Ctrl+Shift+Space");
    expect(chordLabel(DEFAULT_CHORDS.read, true)).toBe("Control+Shift+L");
    expect(chordLabel({ ...DEFAULT_CHORDS.read, code: "Slash" }, false)).toBe("Ctrl+Shift+/");
  });
});
