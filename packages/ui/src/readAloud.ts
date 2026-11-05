// Read aloud from any page or app. The Workshop shell owns the one reader (its
// voice, speed and floating player); a page asks it to read through this module
// and never plays audio itself. The host is kept on the shell's window, so an
// app in a same-origin frame reaches the same reader as one mounted in the page.
// Outside a Workshop shell there is no host and `available` is false, so a
// Read aloud control simply does not show.

import { useCallback, useSyncExternalStore } from "react";

export type ReadAloudPhase = "idle" | "loading" | "playing" | "paused" | "error";

export type ReadAloudState = {
  phase: ReadAloudPhase;
  /** Who asked: the id the reading was started with. */
  source: string | null;
};

export interface ReadAloudHost {
  /** Read `text` (plain or markdown). Call from the click itself: iOS only
   *  plays sound started inside a gesture. `place` is where the text shows on
   *  the page (an element, or a selected range), so the reader can mark the
   *  sentence being read there. */
  read(text: string, source: string, place?: Element | Range | null): void;
  /** Declare that `el` reads as `text()` for `source`, so a tap in it while
   *  something is being read reads it from the tapped sentence. Returns the
   *  undo. Absent on a shell without tap to read. */
  place?(el: Element, source: string, text: () => string): () => void;
  stop(): void;
  getState(): ReadAloudState;
  subscribe(fn: () => void): () => void;
}

const KEY = "__workshopReadAloud";
type HostWindow = Window & { [KEY]?: ReadAloudHost };

function hostOn(win: Window | null): ReadAloudHost | null {
  try {
    return (win as HostWindow | null)?.[KEY] ?? null;
  } catch {
    return null; // a cross-origin parent
  }
}

/** The shell's reader, from this window or the nearest same-origin ancestor. */
export function readAloudHost(): ReadAloudHost | null {
  if (typeof window === "undefined") return null;
  let win: Window = window;
  for (let i = 0; i < 4; i++) {
    const found = hostOn(win);
    if (found) return found;
    if (win.parent === win) break;
    win = win.parent;
  }
  return null;
}

/** The shell installs its reader here. Returns the uninstall. */
export function provideReadAloud(host: ReadAloudHost): () => void {
  (window as HostWindow)[KEY] = host;
  return () => {
    if ((window as HostWindow)[KEY] === host) delete (window as HostWindow)[KEY];
  };
}

const IDLE: ReadAloudState = { phase: "idle", source: null };
const noop = () => () => {};

/** A Read aloud control's view of the reader: whether there is one, whether it
 *  is reading for `source` right now, and the two verbs. */
export function useReadAloud(source: string) {
  const host = readAloudHost();
  const state = useSyncExternalStore(
    host ? host.subscribe : noop,
    host ? host.getState : () => IDLE,
    () => IDLE,
  );
  const mine = state.source === source && state.phase !== "idle" && state.phase !== "error";
  const read = useCallback(
    (text: string, place?: Element | Range | null) => host?.read(text, source, place),
    [host, source],
  );
  const stop = useCallback(() => host?.stop(), [host]);
  return { available: !!host, reading: mine, read, stop, place: host?.place };
}
