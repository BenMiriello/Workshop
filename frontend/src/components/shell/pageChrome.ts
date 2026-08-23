// WHAT A DIRECTORY-SHAPED PAGE DRAWS AROUND ITS TREE, as one table.
//
// A directory page (Memory, an app's Files tab) has two completely different
// shapes and the difference is not "the same layout, smaller".
//
// ON A DESKTOP the tree lives in the shell's page column (the directory
// takeover, components/shell/directory.tsx) and the content area is the
// document beside it. There is room for a page header, it is not a repeat of
// anything, and it stays. Nothing below changes desktop.
//
// ON A PHONE the tree IS the content (docs/platform-redesign/
// shell-mockup-spec-2026-08-22.md section 4), and everything a page header
// would say is already on screen 40px above it: the place name is bar one, the
// page name is the selected tab in the page toolbar. So there is no eyebrow, no
// h1, no description, no breadcrumb and no inline primary action; the mockup's
// mobile file browser is full-bleed 52px rows with nothing above them but the
// Up bar. The primary action moves to a bottom action bar, at the thumb, which
// is where Tickets already puts New ticket.
//
// AND AN OPEN DOCUMENT TAKES EVERYTHING. Spec section 6: "the page toolbar
// collapses to 0, the Up bar hides, and any bottom action bar hides." So the
// bottom bar is not simply "on when narrow": a phone reading a note must not
// have New note pinned over the last line of it.
//
// Measured 2026-08-22 by screenshot: /apps/birds/files spent ~290px on chrome
// before its first row and /memory ~600px, on a 390px-wide screen.

export type DirectoryChromeInput = {
  /** The viewport is at least 900px (lib/useMediaQuery `WIDE`). */
  wide: boolean;
  /** A document (a file, a note, a search result) is what the content area is
   *  showing, rather than the tree. */
  documentOpen: boolean;
};

export type DirectoryChrome = {
  /** The page's own `PageHeader`: eyebrow, h1, description, inline actions.
   *  The shell's breadcrumb follows the same answer (see `usePageChrome`). */
  header: boolean;
  /** Page-level controls drawn ABOVE the tree: search, refresh, view switch. */
  controls: boolean;
  /** The primary action, pinned to the bottom of the content region by
   *  `PageActionBar`. */
  actionBar: boolean;
};

export function directoryChrome({ wide, documentOpen }: DirectoryChromeInput): DirectoryChrome {
  if (wide) return { header: true, controls: true, actionBar: false };
  return { header: false, controls: false, actionBar: !documentOpen };
}
