// THE DOCUMENT TAKEOVER, the shell half.
//
// "The editor is a full content takeover on mobile: the page toolbar collapses
// to 0, the Up bar hides, and any bottom action bar hides."
// (docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 6, and
// section 1.1 for the caret: "It force-collapses to 0 while a document is
// open.")
//
// A phone screen is 390px wide and a document needs all of it. With the page
// toolbar, the page's own header and an Up bar stacked above it, an open file
// started below the fold on the surface whose entire job is showing the file.
//
// WHY A CONTEXT AND NOT A PATH TEST. The shell decides everything else about
// itself from `pathname` (see useShell's `fullScreen`), and that works there
// because one route means one shape. It does not work here: whether a document
// is open is a query parameter on the Files tab, a different query parameter on
// Memory, and neither surface's rule belongs in the shell. So the page declares
// it, exactly as a directory page declares itself in directory.tsx, and the
// shell stays ignorant of what a document is.
//
// IT IS DERIVED, NEVER STORED. The toolbar's own open/closed state is a
// remembered preference (shellState.ts `tabs`), and a takeover must not
// overwrite it: closing the document has to restore whatever the operator had.
// So `tabsOpen` is ANDed with this at read time and the memory is untouched.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type TakeoverState = {
  /** Is a document currently taking the whole content area. */
  active: boolean;
  setActive: (active: boolean) => void;
};

const TakeoverContext = createContext<TakeoverState | null>(null);

export function TakeoverProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false);
  const value = useMemo(() => ({ active, setActive }), [active]);
  return <TakeoverContext.Provider value={value}>{children}</TakeoverContext.Provider>;
}

/** The shell's read. Outside a provider there is no takeover, so a test or a
 *  standalone render of the bars still works. */
export function useTakeover(): boolean {
  return useContext(TakeoverContext)?.active ?? false;
}

/**
 * A page declaring that a document is on screen.
 *
 * `active` is the page's own answer and it is expected to be false on a wide
 * screen: a desktop draws the tree beside the file, so there is nothing to take
 * over and the page toolbar is not in the way.
 */
export function useContentTakeover(active: boolean): void {
  const ctx = useContext(TakeoverContext);
  const setActive = ctx?.setActive;
  // The cleanup is what closes it, so an unmount (navigating away with the
  // document still open) cannot leave the toolbar collapsed forever.
  const set = useCallback((next: boolean) => setActive?.(next), [setActive]);
  useEffect(() => {
    if (!active) return;
    set(true);
    return () => set(false);
  }, [active, set]);
}
