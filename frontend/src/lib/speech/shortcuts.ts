// The speech shortcuts, as a rule over a key event.
//
//   dictate  start or stop dictation into the focused field (Ctrl+Shift+Space)
//   read     read the current selection aloud (Ctrl+Shift+L)
//   Esc      stop dictation, only while one is running
//
// The first two are chords the operator can change in Settings > Speech. A
// chord is stored as the PHYSICAL key (`KeyboardEvent.code`) plus modifiers,
// so it survives a keyboard layout that reports another character. The
// defaults use Ctrl, not Cmd, on every platform: Cmd+Shift+Space and
// Cmd+Shift+L are both taken by the Mac and by browsers.

export type KeyLike = {
  key: string;
  code?: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  repeat?: boolean;
};

export type Chord = { code: string; ctrl: boolean; shift: boolean; alt: boolean; meta: boolean };

export type SpeechChords = { dictate: Chord; read: Chord };

export type SpeechAction = "dictate" | "read" | "stop";

export const DEFAULT_CHORDS: SpeechChords = {
  dictate: { code: "Space", ctrl: true, shift: true, alt: false, meta: false },
  read: { code: "KeyL", ctrl: true, shift: true, alt: false, meta: false },
};

const MODIFIER_KEYS = new Set(["Control", "Shift", "Alt", "Meta", "OS", "AltGraph", "CapsLock", "Fn"]);

/** The physical key of an event. Falls back to the character for an event
 *  that carries no `code` (synthetic, or an old engine). */
export function keyCode(e: KeyLike): string {
  if (e.code) return e.code;
  if (e.key === " " || e.key === "Spacebar") return "Space";
  if (/^[a-z]$/i.test(e.key)) return `Key${e.key.toUpperCase()}`;
  if (/^[0-9]$/.test(e.key)) return `Digit${e.key}`;
  return e.key;
}

export function sameChord(a: Chord, b: Chord): boolean {
  return a.code === b.code && a.ctrl === b.ctrl && a.shift === b.shift && a.alt === b.alt && a.meta === b.meta;
}

function matches(e: KeyLike, c: Chord): boolean {
  return e.ctrlKey === c.ctrl && e.shiftKey === c.shift && e.altKey === c.alt && e.metaKey === c.meta
    && keyCode(e) === c.code;
}

/** Which speech action a key press asks for. `dictating` is whether a
 *  dictation is recording or transcribing, the only time Esc belongs to it. */
export function speechShortcut(e: KeyLike, dictating: boolean, chords: SpeechChords): SpeechAction | null {
  if (e.repeat) return null;
  if (e.key === "Escape") {
    const bare = !e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey;
    return dictating && bare ? "stop" : null;
  }
  if (matches(e, chords.dictate)) return "dictate";
  if (matches(e, chords.read)) return "read";
  return null;
}

export type Recorded = { chord: Chord } | { refused: true } | null;

/** What a key press means to a shortcut recorder: a chord, a refusal (a key
 *  with no Ctrl, Alt or Cmd would fire while typing), or null while only
 *  modifiers are held. Esc is the recorder's own cancel, handled before this. */
export function chordFromEvent(e: KeyLike): Recorded {
  if (MODIFIER_KEYS.has(e.key)) return null;
  const chord: Chord = { code: keyCode(e), ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, meta: e.metaKey };
  const fKey = /^F([1-9]|1[0-9]|2[0-4])$/.test(chord.code);
  if (!fKey && !chord.ctrl && !chord.alt && !chord.meta) {
    return { refused: true };
  }
  return { chord };
}

/** A stored chord, or null when the value is not one. */
export function parseChord(v: unknown): Chord | null {
  if (!v || typeof v !== "object") return null;
  const c = v as Record<string, unknown>;
  if (typeof c.code !== "string" || !c.code) return null;
  const flags = [c.ctrl, c.shift, c.alt, c.meta];
  if (!flags.every((f) => typeof f === "boolean")) return null;
  return { code: c.code, ctrl: !!c.ctrl, shift: !!c.shift, alt: !!c.alt, meta: !!c.meta };
}

const KEY_NAMES: Record<string, string> = {
  Space: "Space", Enter: "Enter", Tab: "Tab", Backspace: "Backspace", Escape: "Esc",
  ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right",
  Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Backslash: "\\",
  Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/", Backquote: "`",
};

function keyName(code: string): string {
  if (KEY_NAMES[code]) return KEY_NAMES[code];
  const m = /^(?:Key|Digit|Numpad)(.+)$/.exec(code);
  return m ? m[1] : code;
}

/** The keys of a chord in the order a platform writes them. The Mac names
 *  Control, Option and Cmd; everything else Ctrl, Alt and Win. */
export function chordKeys(c: Chord, mac: boolean): string[] {
  const out: string[] = [];
  if (c.ctrl) out.push(mac ? "Control" : "Ctrl");
  if (c.alt) out.push(mac ? "Option" : "Alt");
  if (c.shift) out.push("Shift");
  if (c.meta) out.push(mac ? "Cmd" : "Win");
  out.push(keyName(c.code));
  return out;
}

export function chordLabel(c: Chord, mac: boolean): string {
  return chordKeys(c, mac).join("+");
}

/** Whether this device writes shortcuts the Mac way. */
export function isMacLike(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}
