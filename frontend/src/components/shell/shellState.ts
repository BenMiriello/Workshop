// WHAT THE SHELL REMEMBERS, and the three rules that were re-specified until
// they were exact. Pure, so they can be tested without a DOM.
//
// The rules, from docs/platform-redesign/navigation-shell-2026-08-20.md §3.3:
//
//   1. Selecting a DIFFERENT place never toggles the column, and the switch
//      hard cuts. Motion means "the same thing changed", not "you moved".
//   2. Tapping the tile of the place you are ALREADY in toggles the column.
//   3. Each place remembers its own page and column state. This is a local
//      preference and deliberately not in the URL: it is where you left a
//      place, not where you are, and a shared link must not carry it.

import type { ShellPlace } from "./places";
import { landingFor } from "./places";

export type PlaceMemory = {
  /** The last path visited inside this place. */
  path?: string;
  /** Explicit column state. Absent means "never touched it, use the default". */
  columnOpen?: boolean;
};

export type ShellMemory = {
  places: Record<string, PlaceMemory>;
  /**
   * The mobile page toolbar's manual override, and the key it was set against.
   *
   * "A manual toggle holds until you change place or page" (operator, session
   * 2), so the override is stored WITH the key it applies to rather than as a
   * bare boolean plus a reset somewhere else. When the key moves on, the
   * override is simply no longer the one being asked about.
   */
  tabs?: { key: string; open: boolean };
};

export const EMPTY_MEMORY: ShellMemory = { places: {} };

/** Is the page column open for this place right now. */
export function columnOpen(memory: ShellMemory, place: ShellPlace): boolean {
  const remembered = memory.places[place.id]?.columnOpen;
  return remembered ?? place.defaultColumnOpen;
}

/** Rule 2: tapping the tile of the place you are already in. */
export function toggleColumn(memory: ShellMemory, place: ShellPlace): ShellMemory {
  const next = !columnOpen(memory, place);
  return {
    ...memory,
    places: {
      ...memory.places,
      [place.id]: { ...memory.places[place.id], columnOpen: next },
    },
  };
}

/** Rule 3: record where you were, so coming back restores it. */
export function rememberPath(memory: ShellMemory, place: ShellPlace, path: string): ShellMemory {
  if (memory.places[place.id]?.path === path) return memory;
  return {
    ...memory,
    places: {
      ...memory.places,
      [place.id]: { ...memory.places[place.id], path },
    },
  };
}

/**
 * Where tapping a place's tile takes you.
 *
 * The remembered path is only honoured while it still belongs to the place;
 * an app that was uninstalled and reinstalled, or a page that was removed,
 * falls back to the place's first page rather than to a 404.
 */
export function openPath(memory: ShellMemory, place: ShellPlace): string {
  const remembered = memory.places[place.id]?.path;
  if (remembered && belongsTo(place, remembered)) return remembered;
  return landingFor(place);
}

function belongsTo(place: ShellPlace, path: string): boolean {
  return place.pages.some((p) => (p.end ? path === p.to : path === p.to || path.startsWith(p.to + "/")));
}

/** The key the mobile toolbar override is scoped to: place plus page. */
export function tabsKey(placeId: string, pageId: string | null): string {
  return `${placeId}:${pageId ?? ""}`;
}

/**
 * Is the mobile page toolbar open.
 *
 * DEFAULT IS OPEN, EVERYWHERE. An earlier build defaulted it closed on an
 * app's own page and the operator reversed that outright: never default to
 * closed.
 */
export function tabsOpen(memory: ShellMemory, key: string): boolean {
  if (memory.tabs && memory.tabs.key === key) return memory.tabs.open;
  return true;
}

export function toggleTabs(memory: ShellMemory, key: string): ShellMemory {
  return { ...memory, tabs: { key, open: !tabsOpen(memory, key) } };
}

// ---- persistence ----------------------------------------------------------
// One key, one shape, and a read that can never throw: a corrupted value is
// indistinguishable from a first visit and both mean "use the defaults".

export const STORAGE_KEY = "ws.shell.v1";

export function parseMemory(raw: string | null): ShellMemory {
  if (!raw) return EMPTY_MEMORY;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return EMPTY_MEMORY;
    const places = (parsed as ShellMemory).places;
    const tabs = (parsed as ShellMemory).tabs;
    return {
      places: places && typeof places === "object" ? places : {},
      ...(tabs && typeof tabs === "object" && typeof tabs.key === "string" ? { tabs } : {}),
    };
  } catch {
    return EMPTY_MEMORY;
  }
}

/* ---- Rule 1's motion half -------------------------------------------------
   Whether the page column may animate is a value, not a side effect, so the
   awkward case is testable without a DOM: toggle the column at A, go to B,
   come back to A. The column must NOT animate on that return, even though the
   path matches the one the toggle happened at.

   Modelling it as "the toggle, and where it happened" plus a settle step run on
   every path read gets that right, because the settle at B already threw the
   grant away. Comparing a bare path against the current one would not. */

/** A granted animation, and the path it was granted at. Null means hard cut. */
export type Motion = { at: string } | null;

/** Rule 2 fired: the operator tapped their own tile. Motion is granted here
 *  and nowhere else. A toggle does not navigate, which is what makes the path
 *  a usable key for the grant. */
export function grantMotion(pathname: string): Motion {
  return { at: pathname };
}

/** Read the grant against where we now are. Any navigation retires it, which
 *  is Rule 1: moving is not "the same thing changed". Call this on every read;
 *  it is idempotent while you stay put. */
export function settleMotion(motion: Motion, pathname: string): Motion {
  return motion && motion.at === pathname ? motion : null;
}
