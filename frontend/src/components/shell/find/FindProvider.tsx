// Whether the find bar is open, what it is searching for, and the one keyboard
// shortcut that opens it. The provider wraps the whole shell because the things
// that open find (the account menu, in the dock and in the phone's bar one) sit
// outside the content region the bar itself lives in.
//
// On the Mac app the menu owns Cmd+F and signals `workshop:find`. In a browser
// the shortcut is listened for on this document and on every app frame in it,
// in the capture phase so an app that handles keys itself cannot swallow it. A
// key pressed while focus is inside a frame goes to the frame's own document
// and never reaches this one, so without this the shortcut is dead whenever an
// app has focus. A frame on another origin cannot be reached and keeps its own.
//
// FIND BELONGS TO A PAGE. Each page (a path) keeps its own open state and search
// text, so leaving a page leaves its bar behind, and coming back to it finds the
// bar as it was. Held in memory, so a reload starts clean.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

type Entry = { open: boolean; query: string };

const CLOSED: Entry = { open: false, query: "" };

type FindApi = {
  /** The page the bar currently belongs to. Changes when the route does. */
  scope: string;
  open: boolean;
  query: string;
  setQuery: (query: string) => void;
  /** Bumps on every request to open, so an already-open bar refocuses. */
  focusTick: number;
  openFind: () => void;
  closeFind: () => void;
};

const FindContext = createContext<FindApi | null>(null);
// Its own context, holding only a boolean, so a disclosure that reads it
// re-renders when a search starts or ends and not on every keystroke.
const FindingContext = createContext(false);

export function useFind(): FindApi {
  const api = useContext(FindContext);
  if (!api) throw new Error("useFind must be used inside FindProvider");
  return api;
}

/** Something is being searched for on this page. What a page keeps out of the
 *  DOM to stay light (a collapsed block, history not loaded yet) comes in while
 *  this is true, so find reaches all of it. False outside the shell. */
export function useFinding(): boolean {
  return useContext(FindingContext);
}

export function FindProvider({ children }: { children: ReactNode }) {
  const { pathname: scope } = useLocation();
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [focusTick, setFocusTick] = useState(0);
  const entry = entries[scope] ?? CLOSED;

  const patch = useCallback(
    (change: Partial<Entry>) =>
      setEntries((all) => ({ ...all, [scope]: { ...(all[scope] ?? CLOSED), ...change } })),
    [scope],
  );
  const openFind = useCallback(() => {
    patch({ open: true });
    setFocusTick((t) => t + 1);
  }, [patch]);
  const closeFind = useCallback(() => patch({ open: false }), [patch]);
  const setQuery = useCallback((query: string) => patch({ query }), [patch]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "f" || e.altKey || e.shiftKey) return;
      if (!(e.metaKey || e.ctrlKey)) return;
      // A code editor has its own search and its own Cmd+F.
      if ((e.target as Element | null)?.closest?.(".cm-editor")) return;
      e.preventDefault();
      openFind();
    };
    window.addEventListener("keydown", onKey, true);
    // The Mac app never delivers Cmd+F to the page: its menu takes the key and
    // sends this instead.
    window.addEventListener("workshop:find", openFind);

    const relay = (frame: HTMLIFrameElement) => {
      try {
        const win = frame.contentWindow;
        win?.removeEventListener("keydown", onKey, true);
        win?.addEventListener("keydown", onKey, true);
      } catch {
        // Not ours to listen on.
      }
    };
    const onLoad = (e: Event) => {
      if (e.target instanceof HTMLIFrameElement) relay(e.target);
    };
    document.querySelectorAll("iframe").forEach(relay);
    document.addEventListener("load", onLoad, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("workshop:find", openFind);
      document.removeEventListener("load", onLoad, true);
    };
  }, [openFind]);

  const api = useMemo(
    () => ({ scope, open: entry.open, query: entry.query, setQuery, focusTick, openFind, closeFind }),
    [scope, entry, setQuery, focusTick, openFind, closeFind],
  );
  return (
    <FindContext.Provider value={api}>
      <FindingContext.Provider value={entry.open && !!entry.query.trim()}>{children}</FindingContext.Provider>
    </FindContext.Provider>
  );
}
