// Chords a browser or the operating system commonly takes before a page sees
// them. A chord on this list may never reach the speech layer, or may close a
// tab when it does, so Settings warns when one is chosen. A warning, not a
// refusal: a kiosk browser or a remapped keyboard can make any of them free.

import type { Chord } from "./shortcuts";

type Taken = {
  code: string;
  /** Cmd on the Mac, Ctrl elsewhere. */
  primary?: boolean;
  ctrl?: boolean;
  alt?: boolean;
  shift?: boolean;
  meta?: boolean;
  /** Only on this platform; both when absent. */
  mac?: boolean;
  what: string;
};

const BROWSER = "Browsers use this";
const TAKEN: Taken[] = [
  { code: "KeyW", primary: true, what: `${BROWSER} to close the tab.` },
  { code: "KeyW", primary: true, shift: true, what: `${BROWSER} to close the window.` },
  { code: "KeyT", primary: true, what: `${BROWSER} to open a tab.` },
  { code: "KeyT", primary: true, shift: true, what: `${BROWSER} to reopen a closed tab.` },
  { code: "KeyN", primary: true, what: `${BROWSER} to open a window.` },
  { code: "KeyN", primary: true, shift: true, what: `${BROWSER} to open a private window.` },
  { code: "KeyQ", primary: true, what: "This quits the browser." },
  { code: "KeyR", primary: true, what: `${BROWSER} to reload the page.` },
  { code: "KeyR", primary: true, shift: true, what: `${BROWSER} to reload the page.` },
  { code: "KeyL", primary: true, what: `${BROWSER} to jump to the address bar.` },
  { code: "KeyF", primary: true, what: `${BROWSER} to find on the page.` },
  { code: "Tab", ctrl: true, what: `${BROWSER} to switch tabs.` },
  { code: "Tab", ctrl: true, shift: true, what: `${BROWSER} to switch tabs.` },
  { code: "KeyA", primary: true, what: "This selects all text." },
  { code: "KeyC", primary: true, what: "This copies." },
  { code: "KeyV", primary: true, what: "This pastes." },
  { code: "KeyX", primary: true, what: "This cuts." },
  { code: "KeyZ", primary: true, what: "This undoes." },
  { code: "Space", meta: true, mac: true, what: "The Mac uses this for Spotlight." },
  { code: "Space", ctrl: true, mac: true, what: "The Mac uses this to switch input sources." },
  { code: "Tab", meta: true, mac: true, what: "The Mac uses this to switch apps." },
  { code: "Tab", alt: true, mac: false, what: "The system uses this to switch windows." },
  { code: "F4", alt: true, mac: false, what: "The system uses this to close the window." },
  { code: "KeyL", meta: true, mac: false, what: "The system uses this to lock the screen." },
];

function asChord(t: Taken, mac: boolean): Chord {
  return {
    code: t.code,
    ctrl: !!t.ctrl || (!!t.primary && !mac),
    meta: !!t.meta || (!!t.primary && mac),
    alt: !!t.alt,
    shift: !!t.shift,
  };
}

/** Why `chord` may not work on this platform, or null when nothing common
 *  takes it. */
export function chordConflict(chord: Chord, mac: boolean): string | null {
  for (const t of TAKEN) {
    if (t.mac !== undefined && t.mac !== mac) continue;
    const c = asChord(t, mac);
    if (c.code === chord.code && c.ctrl === chord.ctrl && c.meta === chord.meta
      && c.alt === chord.alt && c.shift === chord.shift) return t.what;
  }
  return null;
}
