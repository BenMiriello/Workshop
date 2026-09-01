import { describe, expect, it } from "vitest";
import {
  APP_PLACE_PREFIX, NO_BRANCH_LABEL, branchChipLabel, branchSubject, directoryChrome,
} from "./pageChrome";

describe("directoryChrome", () => {
  it("keeps the whole desktop header, and never pins a bottom bar there", () => {
    expect(directoryChrome({ wide: true, documentOpen: false })).toEqual({
      header: true,
      controls: true,
      actionBar: false,
    });
  });

  it("draws nothing above an open document on a desktop either", () => {
    // 2026-08-27 (D4). This used to assert the opposite, on the theory that the
    // desktop tree lives in the page column so an open file is not in the
    // header's way. It is the other way round: the header was in the FILE's
    // way, and the operator read the page's search-and-new row sitting over a
    // note he had opened as the page not knowing what it was showing.
    expect(directoryChrome({ wide: true, documentOpen: true })).toEqual({
      header: false,
      controls: false,
      actionBar: false,
    });
  });

  it("gives a document the same answer on both viewports", () => {
    expect(directoryChrome({ wide: true, documentOpen: true }))
      .toEqual(directoryChrome({ wide: false, documentOpen: true }));
  });

  it("draws no header and no controls above the tree on a phone", () => {
    // The defect this rule exists for: bar one already says the place and the
    // selected tab already says the page.
    const chrome = directoryChrome({ wide: false, documentOpen: false });
    expect(chrome.header).toBe(false);
    expect(chrome.controls).toBe(false);
  });

  it("moves the primary action to the bottom bar on a phone", () => {
    expect(directoryChrome({ wide: false, documentOpen: false }).actionBar).toBe(true);
  });

  it("hides the bottom bar too once a document owns the phone screen", () => {
    // shell-mockup-spec-2026-08-22.md section 6.
    expect(directoryChrome({ wide: false, documentOpen: true })).toEqual({
      header: false,
      controls: false,
      actionBar: false,
    });
  });

  it("never draws a bottom bar and a page header at once", () => {
    for (const wide of [true, false]) {
      for (const documentOpen of [true, false]) {
        const chrome = directoryChrome({ wide, documentOpen });
        expect(chrome.header && chrome.actionBar).toBe(false);
      }
    }
  });
});

describe("branchSubject", () => {
  const apps = [
    { id: "linked", projectSlug: "linked-tree" },
    { id: "unlinked", projectSlug: null },
    { id: "blank", projectSlug: "   " },
    { id: "absent" },
  ];

  it("names the branch of an app that has a linked working tree", () => {
    expect(branchSubject({ id: `${APP_PLACE_PREFIX}linked`, kind: "app" }, apps))
      .toEqual({ appId: "linked", projectSlug: "linked-tree" });
  });

  it("draws nothing for an app with no linked tree", () => {
    // The kernel of design doc section 8.2 that survived the override: most
    // places have no repo, so most places get no control at all. Null, not a
    // subject with an empty slug that the chrome would then have to special-case.
    expect(branchSubject({ id: `${APP_PLACE_PREFIX}unlinked`, kind: "app" }, apps)).toBeNull();
    expect(branchSubject({ id: `${APP_PLACE_PREFIX}absent`, kind: "app" }, apps)).toBeNull();
  });

  it("treats a whitespace-only slug as no tree", () => {
    expect(branchSubject({ id: `${APP_PLACE_PREFIX}blank`, kind: "app" }, apps)).toBeNull();
  });

  it("draws nothing at any platform place", () => {
    // Home has no ref. All chats has no ref. Settings has no ref.
    for (const kind of ["home", "chats", "settings"]) {
      expect(branchSubject({ id: kind, kind }, apps)).toBeNull();
    }
  });

  it("draws nothing for an app the installation does not have", () => {
    expect(branchSubject({ id: `${APP_PLACE_PREFIX}nope`, kind: "app" }, apps)).toBeNull();
  });

  it("pins the id format places.ts builds", () => {
    // If `places.ts` ever stops prefixing an app place with `app:`, this fails
    // here rather than silently emptying the branch control everywhere.
    expect(APP_PLACE_PREFIX).toBe("app:");
    expect(branchSubject({ id: "linked", kind: "app" }, apps)).toBeNull();
  });
});

describe("branchChipLabel", () => {
  it("says so, shortly, when there is no branch to name", () => {
    // "branch", not "ref" and not "worktree"
    // (threads-branches-and-pages-2026-08-29.md section 3).
    expect(NO_BRANCH_LABEL).toBe("no branch");
    expect(branchChipLabel(null)).toBe(NO_BRANCH_LABEL);
    expect(branchChipLabel(undefined)).toBe(NO_BRANCH_LABEL);
    expect(branchChipLabel("   ")).toBe(NO_BRANCH_LABEL);
  });

  it("leaves a name that already fits alone", () => {
    expect(branchChipLabel("main")).toBe("main");
    expect(branchChipLabel("main", 4)).toBe("main");
  });

  it("never exceeds the cap it was given", () => {
    const long = "feat/client-state-consolidation";
    for (const max of [2, 6, 10, 14, 20, 31]) {
      expect(branchChipLabel(long, max).length).toBeLessThanOrEqual(max);
    }
  });

  it("keeps both the head and the tail, because the tail is what identifies it", () => {
    const label = branchChipLabel("feat/client-state-consolidation", 14);
    expect(label).toBe("feat/cl…dation");
    expect(label.startsWith("feat/")).toBe(true);
    expect(label.endsWith("dation")).toBe(true);
  });

  it("distinguishes two branches that share a long prefix", () => {
    const a = branchChipLabel("agent/2026-09-01/toolbar", 12);
    const b = branchChipLabel("agent/2026-09-01/mobile", 12);
    expect(a).not.toBe(b);
  });

  it("degrades to a single ellipsis rather than throwing at an absurd cap", () => {
    expect(branchChipLabel("main", 1)).toBe("…");
  });
});
