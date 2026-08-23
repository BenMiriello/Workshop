// WHAT THE PHONE'S PLACE MENU HOLDS, and where an arrow key goes in it. The
// rules, not the render.
//
// Same split as `places.ts` and `shellState.ts`: this repo has no DOM harness
// and is not getting one, so anything the menu DECIDES lives here where it can
// be executed, and `PlaceMenu.tsx` is left with markup and focus plumbing.
//
// Geometry and contents: docs/platform-redesign/shell-mockup-spec-2026-08-22.md
// section 3, over docs/platform-redesign/navigation-shell-2026-08-20.md 4.3.

import type { ShellPlace } from "./places";

export type PlaceMenuRow = {
  place: ShellPlace;
  /** Where the app currently is. Draws the accent-soft row. */
  current: boolean;
};

export type PlaceMenuSections = {
  /** Home and All chats: platform places, the shorter row. */
  platform: PlaceMenuRow[];
  /** One row per installed app, under an uppercase mono section label. */
  apps: PlaceMenuRow[];
};

/**
 * Split the shell's places into the two groups the menu draws.
 *
 * THREE THINGS THE MOCKUP DRAWS ARE NOT HERE, and each is a later decision the
 * mockup predates:
 *
 *   • no Settings row — the avatar owns account and settings, and two controls
 *     to one destination is the duplication the dock was built to remove. Same
 *     rule the desktop dock already follows (Dock.tsx);
 *   • no Agents group — agents leave the dock (design doc 6.1);
 *   • no ref or branch pill on any row (design doc 8.2): a ref belongs to a
 *     project's repo and most places have no repo at all.
 *
 * Recorded in the spec file's section 7 item 1: on menu CONTENTS the design doc
 * wins over the mockup.
 *
 * Order is the dock's order, so the two navigations name the same things in the
 * same sequence.
 */
export function placeMenuSections(places: ShellPlace[], currentId: string): PlaceMenuSections {
  const rows = (kinds: ShellPlace["kind"][]): PlaceMenuRow[] =>
    places
      .filter((p) => kinds.includes(p.kind))
      .map((place) => ({ place, current: place.id === currentId }));
  return { platform: rows(["home", "chats"]), apps: rows(["app"]) };
}

/** Every row the menu draws, in DOM order. What the arrow keys walk. */
export function placeMenuRows(sections: PlaceMenuSections): PlaceMenuRow[] {
  return [...sections.platform, ...sections.apps];
}

/**
 * Where a key moves focus inside the open menu, or `null` for a key the menu
 * does not handle.
 *
 * `at` is the currently focused row, or -1 when focus is on the panel itself
 * (which is where it starts: focusing a ROW on open draws a focus ring around
 * an entry nobody chose).
 *
 * TAB IS IN HERE ON PURPOSE. The kit's `Menu` closes on Tab and lets focus
 * escape to the page behind it; this menu covers the page with a scrim, so
 * letting focus walk under the scrim would put the keyboard somewhere the
 * pointer cannot go. Tab wraps within the rows instead, which is the trap.
 */
export function menuFocusMove(
  key: string,
  shiftKey: boolean,
  at: number,
  count: number,
): number | null {
  if (count <= 0) return null;
  const forward =
    key === "ArrowDown" || (key === "Tab" && !shiftKey);
  const back = key === "ArrowUp" || (key === "Tab" && shiftKey);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  // Nothing focused yet: forward lands on the first row and back on the last,
  // rather than on whatever index arithmetic on -1 happens to produce.
  if (at < 0 || at >= count) {
    if (forward) return 0;
    if (back) return count - 1;
    return null;
  }
  if (forward) return (at + 1) % count;
  if (back) return (at - 1 + count) % count;
  return null;
}
