import { describe, expect, it } from "vitest";
import { chordConflict } from "./chordConflicts";
import { DEFAULT_CHORDS, type Chord } from "./shortcuts";

const chord = (code: string, mods: Partial<Omit<Chord, "code">> = {}): Chord => ({
  code, ctrl: false, shift: false, alt: false, meta: false, ...mods,
});

describe("chordConflict", () => {
  it("leaves the default chords alone on every platform", () => {
    for (const mac of [true, false]) {
      expect(chordConflict(DEFAULT_CHORDS.dictate, mac)).toBeNull();
      expect(chordConflict(DEFAULT_CHORDS.read, mac)).toBeNull();
    }
  });

  it("warns on the browser's own chords with Ctrl elsewhere and Cmd on the Mac", () => {
    expect(chordConflict(chord("KeyW", { ctrl: true }), false)).toBe("Browsers use this to close the tab.");
    expect(chordConflict(chord("KeyW", { meta: true }), true)).toBe("Browsers use this to close the tab.");
    expect(chordConflict(chord("KeyW", { ctrl: true }), true)).toBeNull();
    for (const code of ["KeyT", "KeyN", "KeyQ", "KeyR", "KeyL"]) {
      expect(chordConflict(chord(code, { ctrl: true }), false)).not.toBeNull();
    }
    expect(chordConflict(chord("Tab", { ctrl: true }), true)).not.toBeNull();
  });

  it("knows the system chords of each platform", () => {
    expect(chordConflict(chord("Space", { meta: true }), true)).toBe("The Mac uses this for Spotlight.");
    expect(chordConflict(chord("Space", { meta: true }), false)).toBeNull();
    expect(chordConflict(chord("Tab", { alt: true }), false)).not.toBeNull();
  });

  it("matches modifiers exactly", () => {
    expect(chordConflict(chord("KeyT", { ctrl: true, shift: true }), false)).toBe("Browsers use this to reopen a closed tab.");
    expect(chordConflict(chord("KeyT", { ctrl: true, alt: true }), false)).toBeNull();
  });
});
