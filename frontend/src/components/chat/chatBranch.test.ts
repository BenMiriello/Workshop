import { describe, expect, it } from "vitest";
import { branchLabel, branchName, branchStatus, showsHolder, type ChatWorktree } from "./chatBranch";
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

describe("branchLabel", () => {
  it("names the trunk by its branch ref, falling back to Main", () => {
    // worktree-as-the-place-2026-08-28 §3: one name, not two. "Shared tree ·
    // main" was replaced by the branch name itself ("main"). The trunk is
    // called what its branch is called.
    expect(branchLabel(tree({ default_ref: "master" }))).toBe("master");
    expect(branchLabel(tree({ default_ref: "main" }))).toBe("main");
    expect(branchLabel(tree({ default_ref: "" }))).toBe("Main");
  });

  it("names the pin when there is one", () => {
    expect(branchLabel(tree({ branch_slug: "fix-42", pinned: true }))).toBe("fix-42");
  });

  it("shortens a machine hex slug, because a toolbar has no room for 32 characters", () => {
    expect(branchLabel(tree({ branch_slug: "t-537437c1d3de4feb98801c3f1416c383" })))
      .toBe("t-537437c1…");
    expect(branchLabel(tree({ branch_slug: "bc81b563abe14333be4e8626dc8182bd" })))
      .toBe("bc81b563…");
  });

  it("says nothing it does not know while cold", () => {
    expect(branchLabel(null)).toBe("Branch");
  });

  it("prefers the declared name over the slug", () => {
    // threads-branches-and-pages-2026-08-29 §3: a branch is named after its
    // work. `display_name` is that name; the slug is how git spells it.
    expect(branchLabel(tree({ branch_slug: "t-9f1c", display_name: "chat toolbar fixes" })))
      .toBe("chat toolbar fixes");
  });
});

describe("branchName", () => {
  // THE MOVE LIST AND THE PILL ANSWER THE SAME QUESTION. The list used to
  // render `branch_slug` raw, which drew three 32-character hex rows inside the
  // control whose whole point is that a branch has a readable name. Measured on
  // this install 2026-09-01: six branches on the wire, `display_name` null on
  // all six, three of them bare hex.
  it("shortens a machine id the same way the pill does", () => {
    expect(branchName({ branch_slug: "bc81b563abe14333be4e8626dc8182bd" })).toBe("bc81b563…");
    expect(branchName({ branch_slug: "t-537437c1d3de4feb98801c3f1416c383" })).toBe("t-537437c1…");
  });

  it("leaves a name that is a name alone", () => {
    expect(branchName({ branch_slug: "give-the-operator-real-search" }))
      .toBe("give-the-operator-real-search");
  });

  it("takes the declared name when there is one", () => {
    expect(branchName({ branch_slug: "t-537437c1d3de4feb98801c3f1416c383", display_name: "search" }))
      .toBe("search");
  });

  it("has nothing to say about nothing", () => {
    expect(branchName(null)).toBe("");
    expect(branchName({ branch_slug: null })).toBe("");
  });
});

describe("showsHolder", () => {
  // A CONSTANT IS NOT A SIGNAL. Every idle chat is `stopped` here, so a dot on
  // that state would sit on every toolbar in the app saying nothing.
  it("says nothing on a tree nobody is in", () => {
    expect(showsHolder(branchStatus(tree(), null, THREAD))).toBe(false);
    expect(showsHolder(branchStatus(tree({ branch_slug: "b", pinned: true }), null, THREAD)))
      .toBe(false);
  });

  it("shows the dot exactly when somebody is in the tree", () => {
    const yours = branchStatus(
      tree({ branch_slug: "b", pinned: true, held: true, lease: lease(THREAD) }),
      null, THREAD);
    const theirs = branchStatus(
      tree({ branch_slug: "b", pinned: true, blocked_by: lease("other") }), null, THREAD);
    expect(showsHolder(yours)).toBe(true);
    expect(showsHolder(theirs)).toBe(true);
  });
});

describe("branchStatus", () => {
  it("the shared tree is neutral: nothing is held there by design", () => {
    const s = branchStatus(tree(), null, THREAD);
    expect(s).toEqual({ kind: "stopped", label: "Shared" });
  });

  it("a pinned tree this chat holds is yours", () => {
    const s = branchStatus(
      tree({ branch_slug: "fix-42", pinned: true, held: true, lease: lease(THREAD) }),
      null, THREAD);
    expect(s).toEqual({ kind: "ok", label: "Yours" });
  });

  it("a pinned tree nobody holds is free, which is the state between turns", () => {
    // THIS IS THE COMMON CASE AND IT IS NOT A PROBLEM. A chat holds its tree for
    // the length of a TURN, so an idle pinned chat holds nothing.
    const s = branchStatus(tree({ branch_slug: "fix-42", pinned: true }), null, THREAD);
    expect(s).toEqual({ kind: "stopped", label: "Free" });
  });

  it("somebody else's tree is running, even with no directory entry to join", () => {
    const s = branchStatus(
      tree({ branch_slug: "fix-42", pinned: true, blocked_by: lease("other-thread") }),
      null, THREAD);
    expect(s).toEqual({ kind: "running", label: "Held" });
  });

  it("a held tree that has gone quiet warns, and still says Held", () => {
    // Idle is a TIMESTAMP rendered as a warn, never a third state word
    // (workspace-ownership-and-leases section 6).
    const idle = entry({ lease: lease("other-thread", { idle_since: "2026-08-28T00:00:00Z" }) });
    const s = branchStatus(
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
    ].map((t) => branchStatus(t, entry(), THREAD).kind);
    expect(kinds).not.toContain("danger");
  });
});
