import { describe, expect, it } from "vitest";
import { treeLabel, treeStatus, type ChatWorktree } from "./chatTree";
import type { DirectoryEntry } from "../../lib/holders";

const THREAD = "thread-1";

function tree(over: Partial<ChatWorktree> = {}): ChatWorktree {
  return {
    cwd: "/srv/projects/demo",
    project_slug: "demo",
    scope: "project:demo",
    branch_slug: null,
    branch: null,
    source: "app_default",
    read_only: true,
    detail: "",
    subject_id: null,
    lease: null,
    blocked_by: null,
    pinned: false,
    held: false,
    default_ref: "master",
    branches: [],
    can_pin: true,
    ...over,
  } as ChatWorktree;
}

function lease(holder: string, over: Record<string, unknown> = {}) {
  return {
    subject_kind: "worktree", subject_id: "w1", holder_kind: "chat",
    holder_id: holder, generation: 2, state: "held", idle_since: null,
    ...over,
  } as ChatWorktree["lease"];
}

function entry(over: Partial<DirectoryEntry> = {}): DirectoryEntry {
  return {
    session_id: "other-thread", session_kind: "chat", principal: "op",
    activity: "working", scope: "project:demo", subject_kind: "worktree",
    subject_id: "w1", subject_label: null, subject_status: null,
    lease: lease("other-thread"),
    ...over,
  } as DirectoryEntry;
}

describe("treeLabel", () => {
  it("names the trunk by what git actually calls it, not 'main'", () => {
    expect(treeLabel(tree({ default_ref: "master" }))).toBe("master");
  });

  it("names the pin when there is one", () => {
    expect(treeLabel(tree({ branch_slug: "fix-42", pinned: true }))).toBe("fix-42");
  });

  it("says nothing it does not know while cold", () => {
    expect(treeLabel(null)).toBe("Tree");
    // A project whose trunk could not be read must not be labelled "main".
    expect(treeLabel(tree({ default_ref: "" }))).toBe("shared tree");
  });
});

describe("treeStatus", () => {
  it("the shared tree is neutral: nothing is held there by design", () => {
    const s = treeStatus(tree(), null, THREAD);
    expect(s).toEqual({ kind: "stopped", label: "Shared" });
  });

  it("a pinned tree this chat holds is yours", () => {
    const s = treeStatus(
      tree({ branch_slug: "fix-42", pinned: true, held: true, lease: lease(THREAD) }),
      null, THREAD);
    expect(s).toEqual({ kind: "ok", label: "Yours" });
  });

  it("a pinned tree nobody holds is free, which is the state between turns", () => {
    // THIS IS THE COMMON CASE AND IT IS NOT A PROBLEM. A chat holds its tree for
    // the length of a TURN, so an idle pinned chat holds nothing.
    const s = treeStatus(tree({ branch_slug: "fix-42", pinned: true }), null, THREAD);
    expect(s).toEqual({ kind: "stopped", label: "Free" });
  });

  it("somebody else's tree is running, even with no directory entry to join", () => {
    const s = treeStatus(
      tree({ branch_slug: "fix-42", pinned: true, blocked_by: lease("other-thread") }),
      null, THREAD);
    expect(s).toEqual({ kind: "running", label: "Held" });
  });

  it("a held tree that has gone quiet warns, and still says Held", () => {
    // Idle is a TIMESTAMP rendered as a warn, never a third state word
    // (workspace-ownership-and-leases section 6).
    const idle = entry({ lease: lease("other-thread", { idle_since: "2026-08-28T00:00:00Z" }) });
    const s = treeStatus(
      tree({ branch_slug: "fix-42", pinned: true, blocked_by: lease("other-thread") }),
      idle, THREAD);
    expect(s).toEqual({ kind: "warn", label: "Held" });
  });

  it("never reaches danger", () => {
    // Section 14 maps `danger` to "preempted or conflicted", and neither is
    // observable here: a preempted lease is just the next generation held by
    // somebody else. Painting that red would fire on the healthy case.
    const kinds = [
      tree(),
      tree({ branch_slug: "b", pinned: true }),
      tree({ branch_slug: "b", pinned: true, held: true, lease: lease(THREAD) }),
      tree({ branch_slug: "b", pinned: true, blocked_by: lease("x") }),
    ].map((t) => treeStatus(t, entry(), THREAD).kind);
    expect(kinds).not.toContain("danger");
  });
});
