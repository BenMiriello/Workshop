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

// TWO DECLARATIONS, ONE CONTEXT, BECAUSE THEY ANSWER THE SAME QUESTION AT TWO
// DEPTHS: what owns the phone's content area.
//
//   `document` - a file or a note is open. The page toolbar collapses to 0, the
//                Up bar goes, any bottom action bar goes, and the page's own
//                header and the shell's trail go with them (spec section 6).
//   `directory` - the page's CONTENT is its tree, which is the phone shape of
//                every directory-shaped page (spec section 4: "The tree IS the
//                page content"). The page toolbar STAYS, because the selected
//                tab is the page's name; only the page's own header, its
//                description and the shell's trail go.
//
// WHY `directory` EXISTS AT ALL. Reported 2026-08-22, measured by screenshot:
// /apps/<slug>/files opened with a breadcrumb reading "Apps / <App>", a 24px h1
// reading "Files" and a description, roughly 290px, on a 390px screen, under a
// bar one that already said "<App>" and a tab that already said "Files". Every
// line of it repeated something 40px above it. /memory was worse at ~600px. The
// mobile equivalents of a page header already exist and are the bars: the place
// name is bar one, the page name is the selected tab, and the primary action is
// a bottom action bar. So on a phone the header is not "small", it is redundant,
// and spec section 4 draws the first row directly under the Up bar.
//
// It is the page that decides, not the shell: a page knows its own viewport
// answer (`!useIsWide()`), and the shell stays ignorant of what a directory is.
// Same reasoning as the document half above, one level up.
type TakeoverState = {
  /** Is a document currently taking the whole content area. */
  active: boolean;
  setActive: (active: boolean) => void;
  /** Is the page's content its own directory tree (the phone shape). */
  directory: boolean;
  setDirectory: (active: boolean) => void;
};

const TakeoverContext = createContext<TakeoverState | null>(null);

export function TakeoverProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false);
  const [directory, setDirectory] = useState(false);
  const value = useMemo(
    () => ({ active, setActive, directory, setDirectory }),
    [active, directory],
  );
  return <TakeoverContext.Provider value={value}>{children}</TakeoverContext.Provider>;
}

/** The shell's read. Outside a provider there is no takeover, so a test or a
 *  standalone render of the bars still works. */
export function useTakeover(): boolean {
  return useContext(TakeoverContext)?.active ?? false;
}

/**
 * Does the page still draw chrome ABOVE its content: its own `PageHeader` and
 * the shell's breadcrumb. False under either takeover.
 *
 * The two are one read because the two callers (`ShellTrail`, `AppTabPage`)
 * want the same answer, and a caller that had to remember to OR them would
 * eventually forget one, which is exactly how the trail survived on the Files
 * tab while the header did not.
 */
export function usePageChrome(): boolean {
  const ctx = useContext(TakeoverContext);
  return !(ctx?.active || ctx?.directory);
}

/** Set one flag on the context for as long as `active` holds, and clear it on
 *  unmount. The cleanup is what closes it, so navigating away with a document
 *  still open cannot leave the toolbar collapsed forever. */
function useDeclare(set: ((active: boolean) => void) | undefined, active: boolean): void {
  const apply = useCallback((next: boolean) => set?.(next), [set]);
  useEffect(() => {
    if (!active) return;
    apply(true);
    return () => apply(false);
  }, [active, apply]);
}

/**
 * A page declaring that a document is on screen.
 *
 * `active` is the page's own answer and it is expected to be false on a wide
 * screen: a desktop draws the tree beside the file, so there is nothing to take
 * over and the page toolbar is not in the way.
 */
export function useContentTakeover(active: boolean): void {
  useDeclare(useContext(TakeoverContext)?.setActive, active);
}

/**
 * A directory-shaped page declaring that its content IS its tree.
 *
 * Pass `!useIsWide()`: on a desktop the tree is drawn in the page column by the
 * directory takeover (see directory.tsx) and the page header beside it is
 * correct, so this is a phone-only declaration.
 */
export function useDirectoryContent(active: boolean): void {
  useDeclare(useContext(TakeoverContext)?.setDirectory, active);
}
