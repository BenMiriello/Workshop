import { describe, expect, it } from "vitest";
import { Bot } from "lucide-react";
import { buildPlaces } from "./places";
import { menuFocusMove, placeMenuRows, placeMenuSections } from "./placeMenu";

// One glyph stands in for all of them, as in places.test.ts: this module
// decides CONTENTS and focus order, not which icon a row draws.
const icons = new Proxy({} as Record<string, typeof Bot>, { get: () => Bot });

const APPS = [
  { id: "finance", name: "Finance", home: "/apps/finance" },
  { id: "birds", name: "Birds", home: "/apps/birds" },
];

const places = buildPlaces(APPS, icons);

describe("placeMenuSections", () => {
  it("puts Home and All chats above the apps, in the dock's order", () => {
    const { platform, apps } = placeMenuSections(places, "home");
    expect(platform.map((r) => r.place.id)).toEqual(["home", "chats"]);
    expect(apps.map((r) => r.place.id)).toEqual(["app:finance", "app:birds"]);
  });

  it("has no Settings row: the avatar owns settings", () => {
    const ids = placeMenuRows(placeMenuSections(places, "home")).map((r) => r.place.id);
    expect(ids).not.toContain("settings");
    // And the place still exists, so this is the menu declining to draw it
    // rather than the place having been removed.
    expect(places.some((p) => p.id === "settings")).toBe(true);
  });

  it("marks exactly the place you are in", () => {
    const rows = placeMenuRows(placeMenuSections(places, "app:birds"));
    expect(rows.filter((r) => r.current).map((r) => r.place.id)).toEqual(["app:birds"]);
  });

  it("marks nothing when you are somewhere the menu does not draw", () => {
    // Settings is a place with no row. Its own row is not what says "you are
    // here" there; the avatar is.
    const rows = placeMenuRows(placeMenuSections(places, "settings"));
    expect(rows.some((r) => r.current)).toBe(false);
  });

  it("draws no apps section when nothing is installed", () => {
    const bare = placeMenuSections(buildPlaces([], icons), "home");
    expect(bare.apps).toEqual([]);
    expect(bare.platform.map((r) => r.place.id)).toEqual(["home", "chats"]);
  });
});

describe("menuFocusMove", () => {
  it("starts at the first row going forward and the last going back", () => {
    // -1 is focus on the panel itself, which is where the menu opens.
    expect(menuFocusMove("ArrowDown", false, -1, 4)).toBe(0);
    expect(menuFocusMove("ArrowUp", false, -1, 4)).toBe(3);
    expect(menuFocusMove("Tab", false, -1, 4)).toBe(0);
    expect(menuFocusMove("Tab", true, -1, 4)).toBe(3);
  });

  it("wraps in both directions", () => {
    expect(menuFocusMove("ArrowDown", false, 3, 4)).toBe(0);
    expect(menuFocusMove("ArrowUp", false, 0, 4)).toBe(3);
  });

  it("traps Tab inside the menu instead of letting it escape under the scrim", () => {
    expect(menuFocusMove("Tab", false, 0, 4)).toBe(1);
    expect(menuFocusMove("Tab", true, 1, 4)).toBe(0);
    expect(menuFocusMove("Tab", false, 3, 4)).toBe(0);
    expect(menuFocusMove("Tab", true, 0, 4)).toBe(3);
  });

  it("jumps to the ends", () => {
    expect(menuFocusMove("Home", false, 2, 4)).toBe(0);
    expect(menuFocusMove("End", false, 2, 4)).toBe(3);
  });

  it("declines keys it does not own, so they keep their normal meaning", () => {
    expect(menuFocusMove("Escape", false, 1, 4)).toBeNull();
    expect(menuFocusMove("Enter", false, 1, 4)).toBeNull();
    expect(menuFocusMove("a", false, 1, 4)).toBeNull();
  });

  it("moves nowhere in an empty menu", () => {
    expect(menuFocusMove("ArrowDown", false, -1, 0)).toBeNull();
    expect(menuFocusMove("Home", false, -1, 0)).toBeNull();
  });

  it("recovers from a stale index rather than computing a bogus one", () => {
    // The focused element was not in the list (it was re-rendered away).
    expect(menuFocusMove("ArrowDown", false, 9, 4)).toBe(0);
    expect(menuFocusMove("ArrowUp", false, 9, 4)).toBe(3);
  });
});
