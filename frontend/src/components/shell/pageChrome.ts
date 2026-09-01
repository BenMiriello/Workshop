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

// ---- The branch control on the title line -----------------------------------
//
// WHERE A BRANCH GETS NAMED, and — more often — where nothing is drawn at all.
//
// The shell mockup put a branch picker on the desktop title line of every place
// and on every row of the phone's place menu, and
// navigation-shell-2026-08-20.md §8.2 rejected all of it: "a branch belongs to
// a project's repo, and most places have no repo at all. Home has no ref.
// Activity has no ref." That objection was right about the places and wrong
// about the control, and on 2026-09-01 the operator reversed the conclusion
// directly: the picker belongs in the shell's own chrome beside the title, not
// re-drawn inside the body of every app tab page.
//
// So the kernel of §8.2 is what these two rules keep. A place is asked whether
// it HAS a branch, and a place that does not gets no control — not a disabled
// one, not a placeholder, nothing at all.

/** The minimum this rule needs to know about one installed app.
 *
 *  `projectSlug` is the app's linked working tree, resolved by the platform
 *  (`backend/services/app_surface.py`, read in `apps/useAppPlaces.ts`). An app
 *  with none has no repo and therefore no branch to name. */
export type BranchApp = { id: string; projectSlug?: string | null };

/** The place the shell is pointing at, reduced to the two fields this reads. */
export type BranchPlace = { id: string; kind: string };

/** The app whose branch the chrome should name. */
export type BranchSubject = { appId: string; projectSlug: string };

/** How `places.ts` builds an app place's id (`app:<app id>`). Restated here
 *  rather than imported so this stays a pure rule with no place-building in it;
 *  `branchSubject`'s tests pin the format so the two cannot drift silently. */
export const APP_PLACE_PREFIX = "app:";

/**
 * The branch this place is on, or null when the place has no repo behind it.
 *
 * Null is the common answer and it is a real one: Home, All chats, Settings and
 * every app with no linked working tree render no branch control whatsoever.
 */
export function branchSubject(
  place: BranchPlace, apps: readonly BranchApp[],
): BranchSubject | null {
  if (place.kind !== "app") return null;
  if (!place.id.startsWith(APP_PLACE_PREFIX)) return null;
  const appId = place.id.slice(APP_PLACE_PREFIX.length);
  const app = apps.find((a) => a.id === appId);
  const projectSlug = (app?.projectSlug ?? "").trim();
  if (!projectSlug) return null;
  return { appId, projectSlug };
}

/** What the chip says when the branch could not be read. Short, because it sits
 *  in a 252px column and a 52px phone bar.
 *
 *  THE WORD IS BRANCH (threads-branches-and-pages-2026-08-29.md section 3: "the
 *  interface says branch", the worktree is the directory it lives in and is
 *  never a name). Not "ref", which is the git term, and not "worktree". */
export const NO_BRANCH_LABEL = "no branch";

/**
 * The branch name as it fits on a title line.
 *
 * TRUNCATED IN THE MIDDLE, and that is the point of it being a rule. Branch
 * names in this system share their heads (`feat/`, `fix/`, a dated agent
 * prefix) and are distinguished by their tails, so cutting either end alone
 * throws away the half that identifies it. The bar must never grow to fit a
 * name and must never wrap to a second row, so the name is what gives.
 *
 * The full name is still reachable: it is the control's `aria-label` and its
 * hover title, and every row of the menu it opens is the unabbreviated name.
 */
export function branchChipLabel(ref: string | null | undefined, max = 14): string {
  const name = (ref ?? "").trim();
  if (!name) return NO_BRANCH_LABEL;
  if (name.length <= max) return name;
  if (max <= 1) return "…";
  const keep = max - 1;
  const head = Math.ceil(keep / 2);
  const tail = keep - head;
  return name.slice(0, head) + "…" + (tail > 0 ? name.slice(name.length - tail) : "");
}
