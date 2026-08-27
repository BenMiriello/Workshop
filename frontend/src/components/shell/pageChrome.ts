// WHAT A DIRECTORY-SHAPED PAGE DRAWS AROUND ITS TREE, as one table.
//
// A directory page (Memory, an app's Files tab) has two completely different
// shapes and the difference is not "the same layout, smaller".
//
// ON A DESKTOP the tree lives in the shell's page column (the directory
// takeover, components/shell/directory.tsx) and the content area is the
// document beside it. Browsing the tree, there is room for a page header, it is
// not a repeat of anything, and it stays.
//
// AN OPEN DOCUMENT TAKES EVERYTHING ON A DESKTOP TOO (2026-08-27,
// memory-page-and-file-viewer-2026-08-27.md D4). `documentOpen` used to be
// consulted only on a phone, so a desktop drew the page's h1, its description,
// its view switch and its whole search-refresh-new row ABOVE every open note
// and every open file. The operator's report walking Memory: "if we're viewing
// an individual memory file, why would this be here? This doesn't make sense."
// It does not. A document carries its own header (`DocumentBar`) with its own
// name, its own toggle and its own Save, and everything the page row offered is
// either on the tree's toolbar beside it or is about a different document.
//
// So the rule is one rule on both viewports: the tree gets the page's chrome,
// a document gets none. The phone answers are unchanged.
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
  if (documentOpen) return { header: false, controls: false, actionBar: false };
  if (wide) return { header: true, controls: true, actionBar: false };
  return { header: false, controls: false, actionBar: true };
}
