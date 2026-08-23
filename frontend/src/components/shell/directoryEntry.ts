// WHEN COLUMN TWO IS THE TREE, as a value rather than as an effect.
//
// docs/platform-redesign/navigation-shell-2026-08-20.md section 3.4 describes
// the takeover from the page list: you are on a directory page, you tap its row
// again, the list slides out and the tree slides in. That is the rule for a
// column that is ALREADY SHOWING the page list.
//
// It is not the whole rule, and the gap is what the operator saw on 2026-08-22:
// an app place opens with its page column COLLAPSED ("each app should open full
// screen with the secondary toolbar hidden", places.ts `defaultColumnOpen`), so
// arriving at an app's Files tab there is no column, no row to tap twice, and
// therefore no way to reach a tree that is only drawn inside the column. The
// ticket states the missing half directly: "opening a directory page turns
// column two into the file tree".
//
// So ARRIVAL opens it, and only arrival. Once you are on the page the decision
// is yours: backing out to the page list stands for as long as you stay, and
// nothing re-opens the tree behind you. That is the whole distinction, and it
// is why the state carries the page the decision was made FOR rather than a
// bare boolean -- "did the operator close this" and "is this a different page
// than the one they closed" are different questions and a boolean answers only
// the first.
//
// Pure, because this repo has no render harness and is not getting one: the
// awkward cases (leave the page and come back, close and stay, switch between
// two directory pages) are sequences of calls here rather than a DOM.

/** The takeover's state: is the tree over the page list, and for which page
 *  that answer was decided. `pageId` is null when no directory page is
 *  registered, which is every collection page and every phone. */
export type DirectoryEntry = {
  pageId: string | null;
  open: boolean;
};

/** Nothing registered, tree closed. The state a shell starts in. */
export const NO_DIRECTORY: DirectoryEntry = { pageId: null, open: false };

/**
 * A directory page registered, or the last one unregistered.
 *
 * Arriving at a directory page opens its tree; staying on it changes nothing,
 * so a close is not undone by the next render or the next file you open.
 * Leaving only forgets WHICH page the decision belonged to -- `open` is left
 * alone, because the panel stays mounted (section 3.4, correction 3) and the
 * shell hides it by asking whether the page you are on is the registered one.
 */
export function enterDirectory(state: DirectoryEntry, pageId: string | null): DirectoryEntry {
  if (pageId === null) {
    return state.pageId === null ? state : { pageId: null, open: state.open };
  }
  if (state.pageId === pageId) return state;
  return { pageId, open: true };
}

/** The operator worked the toggle: the chevron on the row, the back header, or
 *  a control in the content that asks for the tree. */
export function setDirectoryOpen(state: DirectoryEntry, open: boolean): DirectoryEntry {
  return state.open === open ? state : { ...state, open };
}
