// An overlay app's shortcut, as the manifest writes it ("Mod+Alt+V") and as
// the shell binds it (a chord over the physical key, the speech shortcuts'
// shape, so the recorder and the matcher are shared).
//
// `Mod` is Cmd on a Mac and Ctrl elsewhere, the convention every editor uses
// for a shortcut written once for both. The backend keeps only chords that
// hold Mod, Ctrl, Alt or Cmd, or are a function key; this side reads the same
// grammar and refuses the same things.

import { chordFromEvent, sameChord, type Chord, type KeyLike } from "../speech/shortcuts";

const MODS: Record<string, keyof Omit<Chord, "code"> | "mod"> = {
  mod: "mod", ctrl: "ctrl", control: "ctrl", alt: "alt", option: "alt",
  shift: "shift", meta: "meta", cmd: "meta",
};

const NAMED: Record<string, string> = {
  space: "Space", enter: "Enter", tab: "Tab", backspace: "Backspace",
  up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight",
  "-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight", "\\": "Backslash",
  ";": "Semicolon", "'": "Quote", ",": "Comma", ".": "Period", "/": "Slash", "`": "Backquote",
};

function keyToCode(key: string): string | null {
  const low = key.toLowerCase();
  if (NAMED[low]) return NAMED[low];
  if (/^[a-z]$/.test(low)) return `Key${low.toUpperCase()}`;
  if (/^[0-9]$/.test(low)) return `Digit${low}`;
  if (/^f([1-9]|1[0-9]|2[0-4])$/.test(low)) return low.toUpperCase();
  return null;
}

/** A manifest shortcut as a chord on this device, or null when it is not one
 *  the shell can bind without it firing while the operator types. */
export function parseShortcut(text: unknown, mac: boolean): Chord | null {
  if (typeof text !== "string") return null;
  const parts = text.split("+").map((p) => p.trim());
  if (parts.some((p) => !p)) return null;
  const key = parts.pop() as string;
  const code = keyToCode(key);
  if (!code) return null;
  const chord: Chord = { code, ctrl: false, shift: false, alt: false, meta: false };
  const seen = new Set<string>();
  for (const p of parts) {
    const m = MODS[p.toLowerCase()];
    if (!m || seen.has(m)) return null;
    seen.add(m);
    if (m === "mod") chord[mac ? "meta" : "ctrl"] = true;
    else chord[m] = true;
  }
  const fKey = /^F\d+$/.test(code);
  if (!fKey && !chord.ctrl && !chord.alt && !chord.meta) return null;
  return chord;
}

/** Whether a key press is this chord. */
export function pressed(e: KeyLike, chord: Chord): boolean {
  if (e.repeat) return false;
  const got = chordFromEvent(e);
  return !!got && "chord" in got && sameChord(got.chord, chord);
}
