import { describe, expect, it } from "vitest";
import { Bot } from "lucide-react";
import { buildPlaces, type ShellPlace } from "./places";
import {
  EMPTY_MEMORY,
  columnOpen,
  openPath,
  parseMemory,
  rememberPath,
  tabsKey,
  tabsOpen,
  toggleColumn,
  toggleTabs,
} from "./shellState";

const icons = new Proxy({} as Record<string, typeof Bot>, { get: () => Bot });
const places = buildPlaces([{ id: "birds", name: "Birds", home: "/apps/birds" }], icons);
const home = places.find((p) => p.id === "home") as ShellPlace;
const birds = places.find((p) => p.id === "app:birds") as ShellPlace;

describe("columnOpen", () => {
  it("falls back to the place's default before anyone touches it", () => {
    expect(columnOpen(EMPTY_MEMORY, home)).toBe(true);
    expect(columnOpen(EMPTY_MEMORY, birds)).toBe(false);
  });

  it("toggles, and the toggle is per place", () => {
    const once = toggleColumn(EMPTY_MEMORY, birds);
    expect(columnOpen(once, birds)).toBe(true);
    expect(columnOpen(once, home)).toBe(true); // untouched, still its default

    const twice = toggleColumn(once, birds);
    expect(columnOpen(twice, birds)).toBe(false);
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
    expect(openPath(EMPTY_MEMORY, birds)).toBe("/apps/birds");
  });

  it("restores exactly what you left", () => {
    const m = rememberPath(EMPTY_MEMORY, home, "/work");
    expect(openPath(m, home)).toBe("/work");
  });

  it("restores a sub-route of a page, not just the page", () => {
    const m = rememberPath(EMPTY_MEMORY, birds, "/apps/birds/locations");
    expect(openPath(m, birds)).toBe("/apps/birds/locations");
  });

  it("ignores a remembered path that no longer belongs to the place", () => {
    // A page that was removed, or an app whose routes moved. Falling through to
    // the landing page is right; navigating to a 404 is not.
    const m: typeof EMPTY_MEMORY = { places: { home: { path: "/a-page-that-was-deleted" } } };
    expect(openPath(m, home)).toBe("/");
  });

  it("does not rewrite memory when the path has not moved", () => {
    const m = rememberPath(EMPTY_MEMORY, home, "/work");
    expect(rememberPath(m, home, "/work")).toBe(m);
  });
});

describe("the mobile page toolbar", () => {
  it("defaults to open everywhere, including an app's own page", () => {
    expect(tabsOpen(EMPTY_MEMORY, tabsKey("app:birds", "app"))).toBe(true);
    expect(tabsOpen(EMPTY_MEMORY, tabsKey("home", "dashboard"))).toBe(true);
  });

  it("holds a manual close until the page changes", () => {
    const key = tabsKey("home", "dashboard");
    const closed = toggleTabs(EMPTY_MEMORY, key);
    expect(tabsOpen(closed, key)).toBe(false);
    // Same place, different page: back to the default.
    expect(tabsOpen(closed, tabsKey("home", "tickets"))).toBe(true);
    // Different place entirely: back to the default.
    expect(tabsOpen(closed, tabsKey("app:birds", "app"))).toBe(true);
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
