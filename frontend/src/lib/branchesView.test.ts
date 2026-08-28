// The state ladder, exercised on the rows it was built for.
//
// The tests that matter most here are the ones about ABANDONED WORK, because
// that row is the reason the module exists and it is the one a presence-first
// view (`holders.ts`) cannot produce at all: there is no live session to start
// from, so the branch would render "Nobody here" while somebody's uncommitted
// edits sat in the tree holding a build back.
import { describe, expect, it } from "vitest";

import {
  attentionCount, beatSeconds, blockingFiles, branchState, driftHeadline,
  driftKind, heartbeatNote, humanSeconds, orderRows, shortSeconds,
} from "./branchesView";
import type {
  BranchRow, DriftReport, LeaseRow, LeaseWindows, TreeState,
} from "./branchesView";
import type { BranchRecord, DirectoryEntry } from "./holders";

const WINDOWS: LeaseWindows = {
  lease_ttl_sec: 90, heartbeat_sec: 30, idle_after_sec: 600,
  preempt_grace_sec: 30,
};

function record(over: Partial<BranchRecord> = {}): BranchRecord {
  return {
    id: "wt-1", project_slug: "demo", scope: "project:demo",
    branch_slug: "a-ticket", branch: "wt/demo/a-ticket",
    path: "/trees/a-ticket", base_ref: "origin/main", status: "active",
    ...over,
  } as BranchRecord;
}

function lease(over: Partial<LeaseRow> = {}): LeaseRow {
  return {
    subject_kind: "worktree", subject_id: "wt-1",
    holder_kind: "run", holder_id: "sweep-42",
    generation: 1, state: "held",
    held_for_sec: 300, since_heartbeat_sec: 5, since_work_sec: 5,
    ...over,
  } as LeaseRow;
}

function entry(over: Partial<DirectoryEntry> = {}): DirectoryEntry {
  return {
    session_id: "sweep-42", session_kind: "run", activity: "building",
    subject_kind: "worktree", subject_id: "wt-1",
    ...over,
  } as DirectoryEntry;
}

function row(over: Partial<BranchRow> = {}): BranchRow {
  return {
    record: record(), lease: lease(), holder: entry(),
    tree: { clean: true, entries: 0 }, yieldRequested: false,
    ahead: 0, behind: 0,
    ...over,
  };
}

const CLEAN: TreeState = { clean: true, entries: 0 };
const DIRTY: TreeState = { clean: false, entries: 2 };

describe("branchState", () => {
  it("a held, beating, moving lease is running and wants nothing", () => {
    const s = branchState(row(), WINDOWS);
    expect(s.kind).toBe("running");
    expect(s.label).toBe("Working");
    expect(s.needsAttention).toBe(false);
  });

  it("a held lease whose work has stopped moving is warn, and still says held", () => {
    // The state did not change, so the word does not; what changed is that
    // nothing has been written, which is a reason to look.
    const s = branchState(
      row({ lease: lease({ idle_since: "2026-08-27T00:00:00+00:00" }) }), WINDOWS);
    expect(s.kind).toBe("warn");
    expect(s.label).toBe("Idle");
    expect(s.needsAttention).toBe(true);
    expect(s.detail).toContain("10 minutes");
  });

  it("an expired lease over a CLEAN tree is stopped, not a problem", () => {
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: CLEAN }), WINDOWS);
    expect(s.kind).toBe("stopped");
    expect(s.label).toBe("Expired");
    expect(s.needsAttention).toBe(false);
  });

  it("THE ROW THIS MODULE EXISTS FOR: a dirty tree nobody holds is abandoned", () => {
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: DIRTY }), WINDOWS);
    expect(s.kind).toBe("danger");
    expect(s.label).toBe("Abandoned");
    expect(s.needsAttention).toBe(true);
    expect(s.detail).toContain("hold a build back");
  });

  it("a dirty tree with NO lease at all is abandoned too", () => {
    // Nothing ever took this tree, or the row is gone. The uncommitted work is
    // still stranded, which is the fact that matters.
    const s = branchState(row({ lease: null, holder: null, tree: DIRTY }), WINDOWS);
    expect(s.label).toBe("Abandoned");
    expect(s.kind).toBe("danger");
  });

  it("a dirty tree that someone IS holding is not abandoned; they are working", () => {
    const s = branchState(row({ tree: DIRTY }), WINDOWS);
    expect(s.label).toBe("Working");
  });

  it("an unread tree is never called abandoned", () => {
    // `null` is "not read yet", and guessing either way is wrong: guessing
    // clean hides the row this view exists for, guessing dirty cries abandoned
    // at every branch on every first paint.
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: null }), WINDOWS);
    expect(s.label).toBe("Expired");
    expect(s.kind).not.toBe("danger");
  });

  it("a preempt in flight outranks everything else that is live", () => {
    const s = branchState(row({ yieldRequested: true, tree: DIRTY }), WINDOWS);
    expect(s.kind).toBe("danger");
    expect(s.label).toBe("Preempting");
    expect(s.detail).toContain("30 seconds");
  });

  it("your own lease reads as yours, not as somebody working", () => {
    const s = branchState(row(), WINDOWS, "sweep-42");
    expect(s.kind).toBe("ok");
    expect(s.label).toBe("Yours");
  });

  it("a free branch with a clean tree is ok, not stopped", () => {
    const s = branchState(row({ lease: null, holder: null, tree: CLEAN }), WINDOWS);
    expect(s.kind).toBe("ok");
    expect(s.label).toBe("Free");
  });

  it("a finished record short-circuits the whole ladder", () => {
    expect(branchState(row({ record: record({ status: "merged" }), tree: DIRTY }),
                       WINDOWS).label).toBe("Merged");
    expect(branchState(row({ record: record({ status: "discarded" }), tree: DIRTY }),
                       WINDOWS).label).toBe("Discarded");
  });

  it("uses only the five status kinds", () => {
    const kinds = new Set([
      branchState(row(), WINDOWS).kind,
      branchState(row({ lease: lease({ idle_since: "x" }) }), WINDOWS).kind,
      branchState(row({ lease: null, holder: null, tree: DIRTY }), WINDOWS).kind,
      branchState(row({ lease: lease({ state: "expired" }), holder: null }), WINDOWS).kind,
      branchState(row({ lease: null, holder: null }), WINDOWS).kind,
      branchState(row({ yieldRequested: true }), WINDOWS).kind,
    ]);
    for (const k of kinds) {
      expect(["ok", "running", "stopped", "warn", "danger"]).toContain(k);
    }
  });

  it("names the holder from the lease when no live session is left", () => {
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null }), WINDOWS);
    expect(s.detail).toContain("sweep-42");
  });
});

describe("heartbeatNote", () => {
  it("says nothing while the beat is healthy, because a constant is not a signal", () => {
    expect(heartbeatNote(lease({ since_heartbeat_sec: 12 }), WINDOWS)).toBeNull();
    // exactly two beats is still inside the slack the window was chosen to allow
    expect(heartbeatNote(lease({ since_heartbeat_sec: 60 }), WINDOWS)).toBeNull();
  });

  it("speaks once the gap is past two missed beats, and names the TTL", () => {
    const note = heartbeatNote(lease({ since_heartbeat_sec: 75 }), WINDOWS);
    expect(note).toContain("75 seconds ago");
    expect(note).toContain("90 seconds");
  });

  it("keeps the two numbers apart, because the whole window is under two minutes", () => {
    // `humanSeconds` would call both 75 and 90 "1 minute", producing a sentence
    // that contrasts two numbers while printing one.
    expect(beatSeconds(75)).toBe("75 seconds");
    expect(beatSeconds(90)).toBe("90 seconds");
    expect(beatSeconds(600)).toBe("10 minutes");
  });

  it("says nothing about a lease nobody holds", () => {
    expect(heartbeatNote(lease({ state: "expired", since_heartbeat_sec: 900 }),
                         WINDOWS)).toBeNull();
    expect(heartbeatNote(null, WINDOWS)).toBeNull();
  });
});

describe("humanSeconds / shortSeconds", () => {
  it("rounds down, so a heartbeat age never looks worse than it is", () => {
    expect(humanSeconds(119)).toBe("1 minute");
    expect(humanSeconds(120)).toBe("2 minutes");
  });

  it("walks the units", () => {
    expect(humanSeconds(30)).toBe("30 seconds");
    expect(humanSeconds(600)).toBe("10 minutes");
    expect(humanSeconds(7200)).toBe("2 hours");
    expect(humanSeconds(172800)).toBe("2 days");
    expect(shortSeconds(45)).toBe("45s");
    expect(shortSeconds(3600)).toBe("1h");
    expect(shortSeconds(172800)).toBe("2d");
  });

  it("is empty for a number nobody measured, never a zero", () => {
    expect(humanSeconds(null)).toBe("");
    expect(shortSeconds(undefined)).toBe("");
  });
});

describe("orderRows", () => {
  it("puts what needs a decision above what is merely happening", () => {
    const working = row({ record: record({ id: "w" }) });
    const idle = row({
      record: record({ id: "i" }),
      lease: lease({ idle_since: "2026-08-27T00:00:00+00:00" }),
    });
    const abandoned = row({
      record: record({ id: "a" }), lease: lease({ state: "expired" }),
      holder: null, tree: DIRTY,
    });
    const free = row({ record: record({ id: "f" }), lease: null, holder: null });
    const order = orderRows([free, working, idle, abandoned], WINDOWS)
      .map((r) => r.record.id);
    expect(order).toEqual(["a", "i", "w", "f"]);
  });

  it("within a rung, the longest-standing comes first", () => {
    const older = row({
      record: record({ id: "older" }),
      lease: lease({ idle_since: "x", held_for_sec: 9000 }),
    });
    const newer = row({
      record: record({ id: "newer" }),
      lease: lease({ idle_since: "x", held_for_sec: 60 }),
    });
    expect(orderRows([newer, older], WINDOWS).map((r) => r.record.id))
      .toEqual(["older", "newer"]);
  });

  it("does not mutate its input", () => {
    const rows = [row({ record: record({ id: "a" }) }),
                  row({ record: record({ id: "b" }), lease: null, holder: null })];
    const before = rows.map((r) => r.record.id);
    orderRows(rows, WINDOWS);
    expect(rows.map((r) => r.record.id)).toEqual(before);
  });
});

describe("attentionCount", () => {
  it("counts exactly the rows whose state said so", () => {
    const rows = [
      row(),
      row({ lease: lease({ idle_since: "x" }) }),
      row({ lease: null, holder: null, tree: DIRTY }),
      row({ lease: null, holder: null, tree: CLEAN }),
    ];
    expect(attentionCount(rows, WINDOWS)).toBe(2);
  });
});

describe("drift", () => {
  function report(over: Partial<DriftReport> = {}): DriftReport {
    return {
      surfaces: [], blocked: false, stale: false, canonical: true,
      apply_command: "bash scripts/apply.sh", detail: "",
      ...over,
    } as DriftReport;
  }
  const surface = (over: Record<string, unknown>) => ({
    name: "ui bundle", state: "current", blocking_files: [], detail: "",
    ...over,
  }) as DriftReport["surfaces"][number];

  it("blocked and stale are DIFFERENT colours, because they have different fixes", () => {
    expect(driftKind("blocked")).toBe("danger");
    expect(driftKind("stale")).toBe("warn");
    expect(driftKind("current")).toBe("ok");
    expect(driftKind("undecidable")).toBe("warn");
  });

  it("a blocked surface says the apply step will not clear it, and counts the files", () => {
    const head = driftHeadline(report({
      blocked: true, stale: true,
      surfaces: [surface({
        state: "blocked",
        blocking_files: ["frontend/src/A.tsx", "frontend/src/B.tsx"],
      })],
    }));
    expect(head).toContain("old code");
    expect(head).toContain("will not clear it");
    expect(head).toContain("2 files");
  });

  it("a merely stale surface does not tell anyone to commit anything", () => {
    const head = driftHeadline(report({
      stale: true, surfaces: [surface({ state: "stale" })],
    }));
    expect(head).toContain("has not been applied yet");
    expect(head).not.toContain("committed");
  });

  it("a healthy installation says nothing at all", () => {
    // A permanent green bar is the constant that trains people to stop reading.
    expect(driftHeadline(report({ surfaces: [surface({})] }))).toBeNull();
    expect(driftHeadline(null)).toBeNull();
  });

  it("a linked worktree is called out even when everything else is current", () => {
    const head = driftHeadline(report({ canonical: false, surfaces: [surface({})] }));
    expect(head).toContain("linked worktree");
    expect(head).toContain("invisible until the branch merges");
  });

  it("deduplicates the blocking files across surfaces, because a file commits once", () => {
    expect(blockingFiles(report({
      surfaces: [
        surface({ name: "ui bundle", state: "blocked", blocking_files: ["a.tsx", "b.tsx"] }),
        surface({ name: "backend", state: "blocked", blocking_files: ["b.tsx", "c.py"] }),
      ],
    }))).toEqual(["a.tsx", "b.tsx", "c.py"]);
  });
});
