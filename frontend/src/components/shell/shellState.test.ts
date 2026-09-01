import { describe, expect, it } from "vitest";
import { Bot } from "lucide-react";
import { buildPlaces, type ShellPlace } from "./places";
import {
  EMPTY_MEMORY,
  columnOpen,
  grantMotion,
  openPath,
  parseMemory,
  rememberPath,
  setColumnOpen,
  settleMotion,
  tabsKey,
  tabsOpen,
  toggleColumn,
  toggleTabs,
} from "./shellState";

const icons = new Proxy({} as Record<string, typeof Bot>, { get: () => Bot });
// A FICTIONAL app that ships its own screens: `ownScreen` gives it the row for
// them, above whatever platform tabs it has (places.ts). The id is invented
// because a platform file naming a real app is what
// scripts/check_no_app_names.py counts, fixtures included.
const places = buildPlaces(
  [{ id: "gizmo", name: "Gizmo", home: "/apps/gizmo", ownScreen: true, projectSlug: "gizmo", hasFiles: true }],
  icons,
);
const home = places.find((p) => p.id === "home") as ShellPlace;
const gizmo = places.find((p) => p.id === "app:gizmo") as ShellPlace;

describe("columnOpen", () => {
  it("falls back to the place's default before anyone touches it", () => {
    expect(columnOpen(EMPTY_MEMORY, home)).toBe(true);
    expect(columnOpen(EMPTY_MEMORY, gizmo)).toBe(false);
  });

  it("toggles, and the toggle is per place", () => {
    const once = toggleColumn(EMPTY_MEMORY, gizmo);
    expect(columnOpen(once, gizmo)).toBe(true);
    expect(columnOpen(once, home)).toBe(true); // untouched, still its default

    const twice = toggleColumn(once, gizmo);
    expect(columnOpen(twice, gizmo)).toBe(false);
  });

  it("opens without flipping, for the takeover's control in the content", () => {
    // A toggle from the content side would CLOSE the column for anyone whose
    // column was already open, which is the opposite of asking for the tree.
    const opened = setColumnOpen(EMPTY_MEMORY, gizmo, true);
    expect(columnOpen(opened, gizmo)).toBe(true);
    expect(columnOpen(setColumnOpen(opened, gizmo, true), gizmo)).toBe(true);
    // And it is still per place.
    expect(columnOpen(opened, home)).toBe(true);
  });

  it("returns the same memory when the column is already what was asked for", () => {
    const already = setColumnOpen(EMPTY_MEMORY, home, true);
    expect(already).toBe(EMPTY_MEMORY);
  });

  it("remembers a state equal to the default, so it survives a default change", () => {
    const closed = toggleColumn(EMPTY_MEMORY, home); // true -> false
    const open = toggleColumn(closed, home); // false -> true, explicitly
    expect(open.places.home?.columnOpen).toBe(true);
  });
});

describe("openPath", () => {
  it("opens a place on its first page when nothing is remembered", () => {
    expect(openPath(EMPTY_MEMORY, home)).toBe("/");
    expect(openPath(EMPTY_MEMORY, gizmo)).toBe("/apps/gizmo");
  });

  it("restores exactly what you left", () => {
    const m = rememberPath(EMPTY_MEMORY, home, "/tickets");
    expect(openPath(m, home)).toBe("/tickets");
  });

  it("restores a sub-route of a page, not just the page", () => {
    const m = rememberPath(EMPTY_MEMORY, gizmo, "/apps/gizmo/locations");
    expect(openPath(m, gizmo)).toBe("/apps/gizmo/locations");
  });

  it("ignores a remembered path that no longer belongs to the place", () => {
    // A page that was removed, or an app whose routes moved. Falling through to
    // the landing page is right; navigating to a 404 is not.
    const m: typeof EMPTY_MEMORY = { places: { home: { path: "/a-page-that-was-deleted" } } };
    expect(openPath(m, home)).toBe("/");
  });

  it("does not rewrite memory when the path has not moved", () => {
    const m = rememberPath(EMPTY_MEMORY, home, "/tickets");
    expect(rememberPath(m, home, "/tickets")).toBe(m);
  });
});

describe("the mobile page toolbar", () => {
  it("defaults to open everywhere, including an app's own page", () => {
    expect(tabsOpen(EMPTY_MEMORY, tabsKey("app:gizmo", "app"))).toBe(true);
    expect(tabsOpen(EMPTY_MEMORY, tabsKey("home", "dashboard"))).toBe(true);
  });

  it("holds a manual close until the page changes", () => {
    const key = tabsKey("home", "dashboard");
    const closed = toggleTabs(EMPTY_MEMORY, key);
    expect(tabsOpen(closed, key)).toBe(false);
    // Same place, different page: back to the default.
    expect(tabsOpen(closed, tabsKey("home", "tickets"))).toBe(true);
    // Different place entirely: back to the default.
    expect(tabsOpen(closed, tabsKey("app:gizmo", "app"))).toBe(true);
  });

  it("re-opens on a second toggle of the same key", () => {
    const key = tabsKey("home", "dashboard");
    expect(tabsOpen(toggleTabs(toggleTabs(EMPTY_MEMORY, key), key), key)).toBe(true);
  });
});

describe("parseMemory", () => {
  it("treats a first visit, junk, and the wrong shape identically", () => {
    expect(parseMemory(null)).toEqual(EMPTY_MEMORY);
    expect(parseMemory("not json")).toEqual(EMPTY_MEMORY);
    expect(parseMemory("[1,2,3]")).toEqual({ places: {} });
    expect(parseMemory('"a string"')).toEqual(EMPTY_MEMORY);
  });

  it("round-trips what it wrote", () => {
    const m = toggleTabs(rememberPath(EMPTY_MEMORY, home, "/usage"), tabsKey("home", "usage"));
    expect(parseMemory(JSON.stringify(m))).toEqual(m);
  });

  it("drops a tabs override with no key rather than trusting it", () => {
    expect(parseMemory('{"places":{},"tabs":{"open":false}}').tabs).toBeUndefined();
  });
});

describe("column motion", () => {
  it("animates a toggle, because it happened where you are standing", () => {
    const m = grantMotion("/agents");
    expect(settleMotion(m, "/agents")).not.toBeNull();
  });

  it("hard cuts on any navigation away, in the same read", () => {
    const m = grantMotion("/agents");
    expect(settleMotion(m, "/apps/widgets")).toBeNull();
  });

  it("does not animate on the way BACK to where the toggle happened", () => {
    // The case a bare path comparison gets wrong, and the one that produced the
    // slide-on-switch: toggle at Home, open an app, return to Home.
    let m = grantMotion("/agents");
    m = settleMotion(m, "/apps/widgets"); // leaving retires the grant
    expect(settleMotion(m, "/agents")).toBeNull();
  });

  it("survives repeated reads while you stay put, so it is safe on every render", () => {
    let m = grantMotion("/");
    m = settleMotion(m, "/");
    m = settleMotion(m, "/");
    expect(settleMotion(m, "/")).not.toBeNull();
  });

  it("starts hard-cut: nothing animates until a tile is tapped", () => {
    expect(settleMotion(null, "/")).toBeNull();
  });
});
