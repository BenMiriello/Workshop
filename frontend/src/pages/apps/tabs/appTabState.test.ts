import { describe, expect, it } from "vitest";
import { APP_TABS, canHaveContent, tabsForApp, unlinkedReason } from "./appTabState";

const LINKED = { projectSlug: "sample-app", hasFiles: true };
const BARE = { projectSlug: null, hasFiles: false };

describe("APP_TABS", () => {
  it("is the operator's own order, verbatim", () => {
    expect(APP_TABS.map((t) => t.id)).toEqual([
      "chats", "agents", "memory", "files", "tickets", "runs", "deploys",
    ]);
  });

  it("gives each tab a distinct route suffix", () => {
    const suffixes = APP_TABS.map((t) => t.suffix);
    expect(new Set(suffixes).size).toBe(suffixes.length);
  });
});

describe("unlinkedReason", () => {
  it("never explains a non-empty list", () => {
    expect(unlinkedReason("tickets", null, 3)).toBeNull();
    expect(unlinkedReason("tickets", "sample-app", 3)).toBeNull();
  });

  it("chats and memory are never blamed on missing link", () => {
    expect(unlinkedReason("chats", null, 0)).toBeNull();
    expect(unlinkedReason("memory", null, 0)).toBeNull();
  });

  it("explains an empty project-backed collection on an unlinked app", () => {
    expect(unlinkedReason("tickets", null, 0)).toMatch(/no linked project/);
    expect(unlinkedReason("runs", undefined, 0)).toMatch(/no linked project/);
    expect(unlinkedReason("deploys", null, 0)).toMatch(/no linked project/);
    expect(unlinkedReason("agents", null, 0)).toMatch(/no linked project/);
  });

  it("names the actual collection, not a generic word", () => {
    expect(unlinkedReason("tickets", null, 0)).toContain("tickets");
    expect(unlinkedReason("runs", null, 0)).toContain("runs");
  });

  it("is the ordinary empty state once a project is linked", () => {
    expect(unlinkedReason("tickets", "sample-app", 0)).toBeNull();
    expect(unlinkedReason("runs", "sample-app", 0)).toBeNull();
  });
});

describe("canHaveContent", () => {
  it("keeps chats and memory for every app, linked or not", () => {
    expect(canHaveContent("chats", BARE)).toBe(true);
    expect(canHaveContent("memory", BARE)).toBe(true);
  });

  it("drops the project-backed collections on an app with no tree", () => {
    for (const tab of ["tickets", "runs", "deploys", "agents"] as const) {
      expect(canHaveContent(tab, BARE)).toBe(false);
      expect(canHaveContent(tab, LINKED)).toBe(true);
    }
  });

  it("asks about files separately, because a manifest can declare a root", () => {
    // An app may own a directory without owning a project (app_files.py), so
    // Files does not follow the project link.
    expect(canHaveContent("files", { projectSlug: null, hasFiles: true })).toBe(true);
    expect(canHaveContent("files", { projectSlug: "sample-app", hasFiles: false })).toBe(false);
  });

  it("is availability, not population", () => {
    // A linked app with no tickets YET keeps its Tickets tab: the next one
    // filed lands there, and a nav that comes and goes with a row count moves
    // under the operator.
    expect(canHaveContent("tickets", LINKED)).toBe(true);
  });
});

describe("tabsForApp", () => {
  it("gives an app with no declaration every tab it can have", () => {
    expect(tabsForApp(null, LINKED).map((t) => t.id)).toEqual(APP_TABS.map((t) => t.id));
    expect(tabsForApp(null, BARE).map((t) => t.id)).toEqual(["chats", "memory"]);
  });

  it("takes the app's order when it declares one", () => {
    expect(tabsForApp(["tickets", "chats"], LINKED).map((t) => t.id)).toEqual(["tickets", "chats"]);
  });

  it("still drops a declared tab the app cannot hold", () => {
    // Declaring is narrowing, not conjuring: an app that asks for Deploys with
    // no working tree would get the empty row this whole rule exists to remove.
    expect(tabsForApp(["chats", "deploys"], BARE).map((t) => t.id)).toEqual(["chats"]);
  });

  it("ignores a tab id it has never heard of, and never repeats one", () => {
    expect(tabsForApp(["chats", "chats", "wat"], LINKED).map((t) => t.id)).toEqual(["chats"]);
  });

  it("gives an app that declares an empty list no tabs at all", () => {
    // A real state: an app that is entirely its own screens. It is not the
    // same as declaring nothing, which means "all of them".
    expect(tabsForApp([], LINKED)).toEqual([]);
  });
});
