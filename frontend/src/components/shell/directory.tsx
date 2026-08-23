// THE DIRECTORY TAKEOVER, the shell half.
//
// Specified in docs/platform-redesign/navigation-shell-2026-08-20.md section
// 3.4, and section 8.1 records that it is built even though the Files page it
// was drawn for is not: "The takeover interaction (section 3.4) is still built,
// because Memory needs it."
//
//   Arriving at a directory page turns column two INTO its tree, and after
//   that tapping the page you are already on is the toggle back in. Arrival is
//   part of it because an app place opens with the column collapsed, so there
//   is no row there to tap a second time; see `directoryEntry.ts`.
//
//   Tapping a directory page (Memory) while already on it replaces the page
//   column's contents with a tree. The row rises to the top of column two and
//   stays as a header carrying a back control; the rest of the page list
//   translates out to the left while the tree comes in from the right. Going
//   back restores the page list and DOES NOT CHANGE THE CONTENT: you are still
//   looking at the document. With the list restored the row still reads active
//   and carries a right-pointing chevron back into the tree, so the toggle is
//   symmetric.
//
// Four corrections from that section, each load-bearing and each easy to
// re-break, are honoured here and in the `.ws-pagecol__dir` rules in
// src/styles.css: it is a SLIDE and not a cross-fade, there is NO shadow
// overlay, the tree panel STAYS MOUNTED so its exit animates too, and closing
// the column does NOT reset the tree back to the page list.
//
// THE PAGE OWNS THE TREE; THE SHELL OWNS THE SLOT. A page declares that it is a
// directory (`useDirectoryPage`) and renders its own tree into the slot through
// a portal. The shell never imports Memory, never knows what a scope is, and a
// second directory page later is a second caller rather than a branch here.
// Passing the tree as a ReactNode through context would have worked too and was
// rejected: the element's identity changes on every render of the page, so the
// registration effect would loop unless every caller remembered to memoize it.
// A DOM slot has no identity problem.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { NO_DIRECTORY, enterDirectory, setDirectoryOpen } from "./directoryEntry";

/** What a directory page tells the shell about itself. Two strings, so the
 *  registration effect compares by value and cannot loop. */
export type DirectoryRegistration = {
  /** The `ShellPage.id` of the row that rises to become the header. */
  pageId: string;
  /** What that header row reads, and what the back control names. */
  label: string;
};

type DirectoryState = {
  registration: DirectoryRegistration | null;
  register: (reg: DirectoryRegistration | null) => void;
  /** Is the tree currently over the page list. Survives the column closing. */
  open: boolean;
  setOpen: (open: boolean) => void;
  /** The element inside column two the page portals its tree into. Null until
   *  the column has mounted, which is one render. */
  host: HTMLElement | null;
  setHost: (el: HTMLElement | null) => void;
};

const DirectoryContext = createContext<DirectoryState | null>(null);

export function DirectoryProvider({
  children,
  onNeedColumn,
}: {
  children: ReactNode;
  /** Asking for the tree has to be able to open the column it is drawn in: the
   *  control that asks can be in the content, where the column's own state is
   *  not visible. It OPENS rather than toggles, so it can never shut a column
   *  that was already open. */
  onNeedColumn?: () => void;
}) {
  const [registration, setRegistration] = useState<DirectoryRegistration | null>(null);
  // Whether the tree is over the page list, and which page that answer was
  // decided for. The rule is in `directoryEntry.ts` and is tested there.
  const [entry, setEntry] = useState(NO_DIRECTORY);
  const [host, setHost] = useState<HTMLElement | null>(null);

  const setOpen = useCallback((next: boolean) => {
    setEntry((prev) => setDirectoryOpen(prev, next));
  }, []);
  const open = entry.open;

  const register = useCallback((reg: DirectoryRegistration | null) => {
    setRegistration((prev) => {
      if (prev === reg) return prev;
      if (prev && reg && prev.pageId === reg.pageId && prev.label === reg.label) return prev;
      return reg;
    });
    setEntry((prev) => enterDirectory(prev, reg?.pageId ?? null));
  }, []);

  // THE TREE HAS TO BRING ITS COLUMN WITH IT. It is drawn inside column two,
  // and column two is collapsed by default on an app place, so opening the tree
  // without this shows nothing at all -- which is exactly the defect this
  // replaced. It runs on the EDGE (closed -> open) so that a manual close is
  // not immediately re-opened by the next render, and it OPENS rather than
  // toggles so it can never shut a column that was already open.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) onNeedColumn?.();
    wasOpen.current = open;
  }, [open, onNeedColumn]);

  const value = useMemo(
    () => ({ registration, register, open, setOpen, host, setHost }),
    [registration, register, open, host],
  );

  return <DirectoryContext.Provider value={value}>{children}</DirectoryContext.Provider>;
}

/** The shell's read. Outside a provider it reports "no directory here", so a
 *  test that renders the column on its own still works. */
export function useDirectory(): DirectoryState {
  const ctx = useContext(DirectoryContext);
  const fallbackRegister = useCallback(() => {}, []);
  const fallbackSetOpen = useCallback(() => {}, []);
  const fallbackSetHost = useCallback(() => {}, []);
  const fallback = useMemo<DirectoryState>(
    () => ({
      registration: null,
      register: fallbackRegister,
      open: false,
      setOpen: fallbackSetOpen,
      host: null,
      setHost: fallbackSetHost,
    }),
    [fallbackRegister, fallbackSetOpen, fallbackSetHost],
  );
  return ctx ?? fallback;
}

/**
 * A page declaring itself a directory.
 *
 * `enabled` is false on a phone, where the tree is the page CONTENT and drills
 * in place with `Up` (section 4.4) rather than taking over anything. Registering
 * there would mount the tree a second time inside a column the phone never
 * draws, and pay for its requests twice.
 *
 * Returns the portal host to render the tree into, plus the takeover's state so
 * the page can close it after opening a document.
 */
export function useDirectoryPage(reg: DirectoryRegistration & { enabled?: boolean }) {
  const { register, host, open, setOpen } = useDirectory();
  const { pageId, label, enabled = true } = reg;

  useEffect(() => {
    if (!enabled) return;
    register({ pageId, label });
    return () => register(null);
  }, [register, pageId, label, enabled]);

  return { host: enabled ? host : null, open, setOpen };
}
