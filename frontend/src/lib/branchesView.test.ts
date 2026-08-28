// The state ladder, exercised on the rows it was built for.
//
// The tests that matter most here are the ones about UNSAVED WORK, because that
// row is the reason the module exists and it is the one a presence-first view
// (`holders.ts`) cannot produce at all: there is no live session to start from,
// so the branch would render "Nobody here" while somebody's uncommitted edits
// sat in the tree holding a build back.
//
// FIVE WORDS AND FIVE KINDS ARE NOT THE SAME AXIS. The module's own header says
// why: the WORD is the state and the COLOUR is the urgency, so `Stuck` and
// `Unsaved work` each appear in two kinds and the tests below assert the pair
// rather than the word alone.
import { describe, expect, it } from "vitest";

import {
  attentionCount, beatSeconds, branchLabel, branchState, heartbeatNote,
  holderLink, humanSeconds, orderRows, reapPlan, reapUrl, rowFacts,
  SETTLED_AFTER_SEC, shortSeconds, tipAge,
} from "./branchesView";
import type {
  BranchRow, LeaseRow, LeaseWindows, TreeState,
} from "./branchesView";
import { holderName } from "./holders";
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
    recorded: true, presence: "tree",
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
  it("a held, beating, moving lease is running and says In use", () => {
    const s = branchState(row(), WINDOWS);
    expect(s.kind).toBe("running");
    expect(s.label).toBe("In use");
    expect(s.needsAttention).toBe(false);
  });

  it("a held lease whose work has stopped moving is Stuck, and warn", () => {
    const s = branchState(
      row({ lease: lease({ idle_since: "2026-08-27T00:00:00+00:00" }) }), WINDOWS);
    expect(s.kind).toBe("warn");
    expect(s.label).toBe("Stuck");
    expect(s.needsAttention).toBe(true);
    expect(s.detail).toContain("10 minutes");
  });

  it("an expired lease over a CLEAN tree is Free, because it can be taken", () => {
    // It used to say "Expired", which is a fact about a lease row rather than
    // an answer to any of the three questions this page is read for. Nothing
    // is at risk and nothing is held: that is the same decision as a branch
    // nobody ever took, so it is the same word.
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: CLEAN }), WINDOWS);
    expect(s.kind).toBe("ok");
    expect(s.label).toBe("Free");
    expect(s.needsAttention).toBe(false);
  });

  it("THE ROW THIS MODULE EXISTS FOR: a dirty tree nobody holds is Unsaved work", () => {
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: DIRTY }), WINDOWS);
    expect(s.kind).toBe("warn");
    expect(s.label).toBe("Unsaved work");
    expect(s.needsAttention).toBe(true);
    expect(s.detail).toContain("hold a build back");
  });

  it("a dirty tree with NO lease at all is Unsaved work too", () => {
    // Nothing ever took this tree, or the row is gone. The uncommitted work is
    // still stranded, which is the fact that matters.
    const s = branchState(row({ lease: null, holder: null, tree: DIRTY }), WINDOWS);
    expect(s.label).toBe("Unsaved work");
    expect(s.kind).toBe("warn");
  });

  it("a dirty tree that someone IS holding is not stranded; they are in it", () => {
    const s = branchState(row({ tree: DIRTY }), WINDOWS);
    expect(s.label).toBe("In use");
  });

  it("an unread tree is never called unsaved work", () => {
    // `null` is "not read yet", and guessing either way is wrong: guessing
    // clean hides the row this view exists for, guessing dirty cries unsaved
    // work at every branch on every first paint.
    const s = branchState(
      row({ lease: lease({ state: "expired" }), holder: null, tree: null }), WINDOWS);
    expect(s.label).toBe("Free");
    expect(s.kind).not.toBe("warn");
  });

  it("a preempt in flight is Stuck at DANGER, which outranks everything live", () => {
    // Same word as an idle holder, because the decision is the same one (open
    // the holder and deal with it) and a second word for it would be a state
    // the reader has to learn. The COLOUR is what says this one is louder.
    const s = branchState(row({ yieldRequested: true, tree: DIRTY }), WINDOWS);
    expect(s.kind).toBe("danger");
    expect(s.label).toBe("Stuck");
    expect(s.detail).toContain("30 seconds");
  });

  it("your own lease is In use like anyone else's, because the action is the same", () => {
    // "Yours" was a separate rung and it led nowhere different: a tree you are
    // working in and a tree somebody else is working in are both trees to leave
    // alone. The detail sentence still says which.
    const s = branchState(row(), WINDOWS, "sweep-42");
    expect(s.kind).toBe("running");
    expect(s.label).toBe("In use");
    expect(s.detail).toContain("You hold");
  });

  it("a free branch with a clean tree is ok, not stopped", () => {
    const s = branchState(row({ lease: null, holder: null, tree: CLEAN }), WINDOWS);
    expect(s.kind).toBe("ok");
    expect(s.label).toBe("Free");
  });

  it("every way for a branch to be over says Done, and says which in the detail", () => {
    const merged = branchState(row({ record: record({ status: "merged" }), tree: DIRTY }),
                               WINDOWS);
    const discarded = branchState(row({ record: record({ status: "discarded" }), tree: DIRTY }),
                                  WINDOWS);
    const gone = branchState(row({ record: record({ presence: "gone" }), tree: null }),
                             WINDOWS);
    for (const s of [merged, discarded, gone]) {
      expect(s.label).toBe("Done");
      expect(s.kind).toBe("stopped");
      expect(s.needsAttention).toBe(false);
    }
    expect(merged.detail).toContain("trunk");
    expect(discarded.detail).toContain("thrown away");
    expect(gone.detail).toContain("directory is not");
  });

  it("never uses a word outside the five", () => {
    const labels = new Set([
      branchState(row(), WINDOWS).label,
      branchState(row({ lease: lease({ idle_since: "x" }) }), WINDOWS).label,
      branchState(row({ lease: null, holder: null, tree: DIRTY }), WINDOWS).label,
      branchState(row({ lease: lease({ state: "expired" }), holder: null }), WINDOWS).label,
      branchState(row({ lease: null, holder: null }), WINDOWS).label,
      branchState(row({ yieldRequested: true }), WINDOWS).label,
      branchState(row({ record: record({ status: "merged" }) }), WINDOWS).label,
      branchState(row({ record: record({ presence: "gone" }) }), WINDOWS).label,
      branchState(row({ record: record({ presence: "orphan" }) }), WINDOWS).label,
    ]);
    for (const l of labels) {
      expect(["In use", "Stuck", "Unsaved work", "Free", "Done"]).toContain(l);
    }
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
      row({ lease: lease({ state: "expired" }), holder: null, tree: DIRTY }), WINDOWS);
    expect(s.detail).toContain("never committed");
    expect(branchState(row({ lease: lease({ state: "expired" }), holder: null }),
                       WINDOWS).detail).toContain("sweep-42");
  });
});

describe("holderLink", () => {
  // THE ROW LINKS TO THE CHAT, which is what the operator asked this page for.
  it("takes a chat-held tree to that conversation, by thread id", () => {
    const link = holderLink(row({
      lease: lease({ holder_kind: "chat", holder_id: "abc123" }),
      holder: null,
    }));
    expect(link?.to).toBe("/chat/project%3Ademo?thread=abc123");
  });

  it("prefers the live session's own scope, because the lease row has none", () => {
    const link = holderLink(row({
      lease: lease({ holder_kind: "chat", holder_id: "abc123" }),
      holder: entry({ session_kind: "chat", session_id: "abc123", scope: "general" }),
    }));
    expect(link?.to).toBe("/chat/general?thread=abc123");
  });

  it("takes a run-held tree to the run", () => {
    expect(holderLink(row())?.to).toBe("/runs/sweep-42");
  });

  it("answers null rather than a link that would 404", () => {
    expect(holderLink(row({
      lease: lease({ holder_kind: "sweep" }), holder: null,
    }))).toBeNull();
    expect(holderLink(row({ lease: null, holder: null }))).toBeNull();
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

describe("the settled split: one word, two colours", () => {
  // THE OPERATOR'S RULE, and it predates this page: never make a card's alarm
  // colour permanent, because a hue that never varies carries no information.
  // /branches opened on eleven rows in `danger` and nothing else, which is a
  // screen of identical red saying "everything is equally wrong". Eleven trees
  // of stranded work is what this system looks like at rest.
  //
  // The WORD does not change across the split, because the decision does not:
  // both rows are "there is work here nobody saved". What changes is how loudly
  // it is drawn, and the age printed on the row beside it is what makes the two
  // shades readable.
  const NOW = Date.parse("2026-08-28T12:00:00Z");
  const at = (secsAgo: number) => new Date(NOW - secsAgo * 1000).toISOString();
  const stranded = (tip_at: string | null) => row({
    record: record({ tip_at }), lease: null, holder: null, tree: DIRTY,
  });

  it("work stranded in the last day wants a look", () => {
    const s = branchState(stranded(at(3600)), WINDOWS, null, NOW);
    expect(s.label).toBe("Unsaved work");
    expect(s.kind).toBe("warn");
    expect(s.needsAttention).toBe(true);
  });

  it("the same tree a week later is the same word, quietly", () => {
    const s = branchState(stranded(at(7 * 24 * 3600)), WINDOWS, null, NOW);
    expect(s.label).toBe("Unsaved work");
    expect(s.kind).toBe("stopped");
    expect(s.needsAttention).toBe(false);
    expect(s.detail).toContain("7 days");
  });

  it("the boundary is SETTLED_AFTER_SEC, and it is not a lease window", () => {
    expect(branchState(stranded(at(SETTLED_AFTER_SEC - 60)), WINDOWS, null, NOW).kind)
      .toBe("warn");
    expect(branchState(stranded(at(SETTLED_AFTER_SEC + 60)), WINDOWS, null, NOW).kind)
      .toBe("stopped");
  });

  it("an undated tree is never quieted, because nothing measured its age", () => {
    // Quieting a row on a fact nobody recorded is the one way this rung could
    // hide real stranded work.
    expect(branchState(stranded(null), WINDOWS, null, NOW).kind).toBe("warn");
  });

  it("a settled tree drops out of the attention count", () => {
    const rows = [stranded(at(7 * 24 * 3600)), stranded(at(600))];
    expect(attentionCount(rows, WINDOWS, null, NOW)).toBe(1);
  });

  it("and sorts below everything live, but above the rows holding nothing", () => {
    const settled = { ...stranded(at(7 * 24 * 3600)), record: record({ id: "d", tip_at: at(7 * 24 * 3600) }) };
    const done = row({ record: record({ id: "g", presence: "gone" }), lease: null, holder: null });
    const inUse = row({ record: record({ id: "w" }) });
    expect(orderRows([done, settled, inUse], WINDOWS, null, NOW).map((r) => r.record.id))
      .toEqual(["w", "d", "g"]);
  });
});

describe("branchLabel", () => {
  it("shortens a machine hex slug to a prefix that says there is more", () => {
    // `bc81b563abe14333be4e8626dc8182bd` wrapped over two lines, eleven times,
    // and was most of the page's height while identifying nothing a prefix
    // would not.
    expect(branchLabel("bc81b563abe14333be4e8626dc8182bd")).toBe("bc81b563…");
    expect(branchLabel("t-537437c1d3de4feb98801c3f1416c383")).toBe("t-537437c1…");
  });

  it("leaves a name somebody chose completely alone", () => {
    // Abbreviating a real name is how a page becomes unreadable in the other
    // direction.
    expect(branchLabel("t-smoke2")).toBe("t-smoke2");
    expect(branchLabel("feat/client-state-consolidation")).toBe("feat/client-state-consolidation");
    expect(branchLabel("deadbeef")).toBe("deadbeef");
  });

  it("prefers display_name when given a row object", () => {
    const slug = "bc81b563abe14333be4e8626dc8182bd";
    expect(branchLabel({ branch_slug: slug, display_name: "Chat toolbar: pill, rename, new chat" }))
      .toBe("Chat toolbar: pill, rename, new chat");
  });

  it("falls back to slug abbreviation when display_name is null", () => {
    const slug = "bc81b563abe14333be4e8626dc8182bd";
    expect(branchLabel({ branch_slug: slug, display_name: null })).toBe("bc81b563…");
    expect(branchLabel({ branch_slug: slug, display_name: undefined })).toBe("bc81b563…");
  });
});

describe("rowFacts", () => {
  const NOW = Date.parse("2026-08-28T12:00:00Z");

  it("leads with the state WORD, not just the dot's colour", () => {
    const r = row();
    expect(rowFacts(r, branchState(r, WINDOWS, null, NOW))[0]).toBe("In use");
  });

  it("names the holder when one is there, and counts the files when none is", () => {
    const held = row();
    expect(rowFacts(held, branchState(held, WINDOWS, null, NOW)))
      .toEqual(["In use", holderName(entry())]);

    // NOT "2 uncommitted": the word beside it already says the work is
    // unsaved, so the second fact would spend the row's width restating the
    // first. What varies between two of these rows is the SIZE.
    const loose = row({ lease: null, holder: null, tree: DIRTY });
    expect(rowFacts(loose, branchState(loose, WINDOWS, null, NOW)))
      .toEqual(["Unsaved work", "2 files"]);
  });

  it("says one file, not one files", () => {
    const one = row({ lease: null, holder: null, tree: { clean: false, entries: 1 } });
    expect(rowFacts(one, branchState(one, WINDOWS, null, NOW))[1]).toBe("1 file");
  });

  it("says nothing more when there is nothing more that varies", () => {
    const free = row({ lease: null, holder: null, tree: CLEAN });
    expect(rowFacts(free, branchState(free, WINDOWS, null, NOW))).toEqual(["Free"]);
  });
});

describe("orderRows", () => {
  it("ranks by the KIND, so the loudest colour is always on top", () => {
    // Order is derived from the status kind rather than from the word, which is
    // what stops the sort and the paint from disagreeing. The one split inside
    // a kind is at the bottom: a stopped tree still holding work sorts above a
    // stopped record holding none.
    const NOW = Date.parse("2026-08-28T12:00:00Z");
    const preempting = row({ record: record({ id: "p" }), yieldRequested: true });
    const idle = row({
      record: record({ id: "i" }),
      lease: lease({ idle_since: "2026-08-27T00:00:00+00:00", held_for_sec: 9000 }),
    });
    const stranded = row({
      record: record({ id: "a", tip_at: new Date(NOW - 3600_000).toISOString() }),
      lease: lease({ state: "expired" }), holder: null, tree: DIRTY,
    });
    const inUse = row({ record: record({ id: "w" }) });
    const free = row({ record: record({ id: "f" }), lease: null, holder: null });
    const settled = row({
      record: record({ id: "d", tip_at: new Date(NOW - 8 * 24 * 3600_000).toISOString() }),
      lease: null, holder: null, tree: DIRTY,
    });
    const done = row({
      record: record({ id: "g", presence: "gone" }), lease: null, holder: null,
    });
    const order = orderRows(
      [done, settled, free, inUse, stranded, idle, preempting], WINDOWS, null, NOW)
      .map((r) => r.record.id);
    expect(order).toEqual(["p", "i", "a", "w", "f", "d", "g"]);
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

describe("presence", () => {
  it("a record whose directory is gone is Done and wants nothing", () => {
    const s = branchState(row({
      record: record({ presence: "gone" }), lease: null, holder: null, tree: null,
    }), WINDOWS);
    expect(s.kind).toBe("stopped");
    expect(s.label).toBe("Done");
    expect(s.needsAttention).toBe(false);
    expect(s.detail).toContain("directory is not");
  });

  it("a directory git has never heard of is Unsaved work, and wants a look", () => {
    // "Orphaned" was its own word and it named a CAUSE (a teardown that could
    // not finish) rather than a decision. The decision is the same one a dirty
    // tree gets, at its worst: nothing can read what is in here, so do not
    // delete it blindly. Same word, and the detail says why this one is
    // unreadable.
    const s = branchState(row({
      record: record({ presence: "orphan", recorded: false, status: null }),
      lease: null, holder: null, tree: null,
    }), WINDOWS);
    expect(s.kind).toBe("warn");
    expect(s.label).toBe("Unsaved work");
    expect(s.needsAttention).toBe(true);
    expect(s.detail).toContain("Git has no worktree");
  });

  it("A TREE WITH NO RECORD IS STILL RANKED BY THE LADDER, not skipped", () => {
    // The structurally common case: most trees were never allocated through the
    // record path, so `status` is null and `recorded` is false, and the row must
    // still reach `Unsaved work` on a dirty tree nobody holds.
    const s = branchState(row({
      record: record({ recorded: false, status: null }),
      lease: null, holder: null, tree: DIRTY,
    }), WINDOWS);
    expect(s.label).toBe("Unsaved work");
    expect(s.kind).toBe("warn");
  });

  it("puts an unreadable directory above the branches that are merely in use", () => {
    const orphan = row({
      record: record({ id: "orphan", presence: "orphan" }),
      lease: null, holder: null, tree: null,
    });
    const working = row({ record: record({ id: "working" }) });
    const gone = row({
      record: record({ id: "gone", presence: "gone" }),
      lease: null, holder: null, tree: null,
    });
    expect(orderRows([working, gone, orphan], WINDOWS).map((r) => r.record.id))
      .toEqual(["orphan", "working", "gone"]);
  });
});

// --- the verb ----------------------------------------------------------------

describe("reapPlan", () => {
  it("refuses while somebody holds the tree, and says who", () => {
    const plan = reapPlan(row());
    expect(plan.offered).toBe(false);
    expect(plan.blockedBecause).toContain("holds this tree");
  });

  it("refuses on a gone record: there is no directory to remove", () => {
    const plan = reapPlan(row({
      record: record({ presence: "gone" }), lease: null, holder: null, tree: null,
    }));
    expect(plan.offered).toBe(false);
    expect(plan.blockedBecause).toContain("no directory to remove");
  });

  it("refuses until the tree has been READ, because the dialog cannot name the cost", () => {
    const plan = reapPlan(row({ lease: null, holder: null, tree: null }));
    expect(plan.offered).toBe(false);
    expect(plan.blockedBecause).toContain("not been read");
  });

  it("promises a snapshot for a dirty tree, and does not force", () => {
    const r = row({ lease: null, holder: null, tree: DIRTY });
    const plan = reapPlan(r);
    expect(plan.offered).toBe(true);
    expect(plan.force).toBe(false);
    expect(plan.consequence).toContain("2 uncommitted entries");
    expect(plan.consequence).toContain("refs/wip/a-ticket");
    expect(reapUrl(r, plan)).toBe("/api/worktrees/demo/a-ticket");
  });

  it("keeps the tip of a CLEAN branch that is ahead: teardown deletes the ref", () => {
    const plan = reapPlan(row({
      lease: null, holder: null, tree: CLEAN, ahead: 3,
    }));
    expect(plan.consequence).toContain("3 commits ahead");
    expect(plan.consequence).toContain("refs/wip/a-ticket");
  });

  it("promises nothing kept when the branch has nothing the trunk lacks", () => {
    const plan = reapPlan(row({
      lease: null, holder: null, tree: CLEAN, ahead: 0,
    }));
    expect(plan.offered).toBe(true);
    expect(plan.consequence).not.toContain("refs/wip");
  });

  it("AN ORPHAN IS THE ONE PERMANENT ONE, and the request says force", () => {
    // Nothing can snapshot a directory git has no metadata for, so the dialog
    // has to say the removal is permanent rather than promise a recovery.
    const r = row({
      record: record({ presence: "orphan" }), lease: null, holder: null, tree: null,
    });
    const plan = reapPlan(r);
    expect(plan.offered).toBe(true);
    expect(plan.force).toBe(true);
    expect(plan.consequence).toContain("permanently");
    expect(reapUrl(r, plan)).toBe("/api/worktrees/demo/a-ticket?force=true");
  });
});

describe("tipAge", () => {
  const now = Date.parse("2026-08-27T00:00:00Z");

  it("reads the branch tip, which is the only age an unrecorded tree has", () => {
    expect(tipAge(record({
      tip_at: "2026-08-23T00:00:00Z", created_at: null,
    }), now)).toBe("4d");
  });

  it("falls back to the record's own creation when git dated nothing", () => {
    expect(tipAge(record({
      tip_at: null, created_at: "2026-08-26T22:00:00Z",
    }), now)).toBe("2h");
  });

  it("answers null rather than a placeholder when nothing dated it", () => {
    expect(tipAge(record({ tip_at: null, created_at: null }), now)).toBeNull();
  });
});
