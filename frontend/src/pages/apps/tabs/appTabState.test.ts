import { describe, expect, it } from "vitest";
import { APP_TABS, unlinkedReason } from "./appTabState";

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
