import { describe, expect, it } from "vitest";
import {
  THREAD_ARMED_NOTE, branchName, buildWorkRows, changeForBranch, changeNote,
  changesAddr, holderText, leaseHolderId, noChangeNote, rowFacts, rowState,
  sortRows, sortThreads, threadHoldsLease, threadRole, threadStatus,
  threadsByBranch, workQueryKeys,
  type WorkAgent, type WorkRow, type WorkThread,
} from "./workView";
import type { BranchRecord, DirectoryEntry, LeaseRef } from "./holders";
import type { Change } from "./changeReview";

// One branch, one live chat holding it, one change on it. Each test overrides
// the field it is about, so a new field on the API cannot silently change what
// these assert.

const record: BranchRecord = {
  id: "wt-1",
  project_slug: "workshop",
  scope: "project:workshop",
  branch: "feat/widths",
  branch_slug: "feat-widths",
  display_name: null,
  base_ref: "origin/main",
  path: "/srv/branches/feat-widths",
  recorded: true,
  presence: "tree",
  status: "open",
  ticket_id: "d8676bba9f0e4c1b",
  created_at: "2026-08-30T10:00:00Z",
  tip_at: "2026-08-31T10:00:00Z",
};

const lease: LeaseRef = {
  subject_kind: "worktree",
  subject_id: "wt-1",
  holder_kind: "chat",
  holder_id: "thread-a",
  principal_user_id: "019ffdf0",
  generation: 1,
  state: "held",
  idle_since: null,
  expires_at: "2026-09-01T12:01:30Z",
  acquired_at: "2026-09-01T11:50:00Z",
  last_renewed_at: "2026-09-01T11:59:48Z",
  last_work_at: "2026-09-01T11:59:48Z",
};

const holder: DirectoryEntry = {
  session_id: "thread-a",
  session_kind: "chat",
  principal: "operator",
  activity: "editing the width tokens",
  scope: "project:workshop",
  subject_kind: "worktree",
  subject_id: "wt-1",
  subject_label: "feat-widths",
  subject_status: "open",
  pid: 4118779,
  hostname: "bgmdebian",
  last_beat_at: "2026-09-01T11:59:48Z",
  expires_at: "2026-09-01T12:29:48Z",
  lease,
};

const threadA: WorkThread = {
  id: "thread-a",
  title: "Fix the page width",
  last_message: "on it",
  message_count: 12,
  mode: "build",
  running: false,
  branch_slug: "feat-widths",
  context_key: "workshop",
  agent_id: "agent-1",
  last_user_at: "2026-09-01T11:00:00Z",
};

const threadB: WorkThread = {
  ...threadA,
  id: "thread-b",
  title: "Second opinion",
  agent_id: null,
  running: false,
  last_user_at: "2026-09-01T09:00:00Z",
};

const change: Change = {
  id: "chg-1",
  project_slug: "workshop",
  branch_slug: "feat-widths",
  base_ref: "main",
  head_sha: "0f1e2d3c4b5a69788796a5b4c3d2e1f001020304",
  status: "ready",
  title: "Cap the page measure in one place",
  summary: "",
  auto_merge: false,
  ticket_ids: ["d8676bba9f0e4c1b"],
  updated_at: "2026-09-01T10:00:00Z",
};

const agents: WorkAgent[] = [
  { id: "agent-1", slug: "widths-maker", name: "Widths", role: "maker" },
  { id: "agent-2", slug: "reviewer", name: "Reviewer", role: "reviewer" },
];

const row = (over: Partial<WorkRow> = {}): WorkRow => ({
  record,
  holder,
  threads: [threadA, threadB],
  change,
  ...over,
});

describe("branchName", () => {
  it("prefers the repo's own display name", () => {
    expect(branchName({ ...record, display_name: "Page widths" })).toBe("Page widths");
  });

  it("keeps a slug somebody chose whole", () => {
    expect(branchName(record)).toBe("feat-widths");
  });

  it("abbreviates a long hex slug, keeping its ticket prefix", () => {
    expect(branchName({ branch_slug: `t-${"a".repeat(32)}`, display_name: null }))
      .toBe("t-aaaaaaaa…");
  });

  it("leaves a short hex run alone: it is a name, not an id", () => {
    expect(branchName({ branch_slug: "abcdef", display_name: null })).toBe("abcdef");
  });
});

describe("ticket and holder labels", () => {
  it("abbreviates the ticket the branch was opened for", () => {
    expect(rowFacts(row(), rowState(row()))).toContain("d8676bba…");
  });

  it("says Nobody rather than nothing when no session is here", () => {
    expect(holderText({ holder: null })).toBe("Nobody");
    expect(holderText({ holder })).toContain("operator");
  });
});

describe("threadsByBranch", () => {
  it("groups the chats that name a branch", () => {
    const byBranch = threadsByBranch([threadA, threadB]);
    expect(byBranch.get("feat-widths")?.map((t) => t.id)).toEqual(["thread-a", "thread-b"]);
  });

  it("drops a chat that is on no branch: it belongs on Chats", () => {
    expect(threadsByBranch([{ ...threadA, branch_slug: null }]).size).toBe(0);
    expect(threadsByBranch([{ ...threadA, branch_slug: "  " }]).size).toBe(0);
  });
});

describe("changeForBranch", () => {
  it("matches on the branch slug and nothing else", () => {
    expect(changeForBranch([change], "feat-widths")?.id).toBe("chg-1");
    expect(changeForBranch([change], "other")).toBeNull();
  });

  it("prefers the change still in play over a settled one, however recent", () => {
    const merged: Change = {
      ...change, id: "chg-0", status: "merged", updated_at: "2026-09-01T23:00:00Z",
    };
    expect(changeForBranch([merged, change], "feat-widths")?.id).toBe("chg-1");
  });

  it("takes the most recently updated among equals", () => {
    const older: Change = { ...change, id: "chg-old", updated_at: "2026-08-01T10:00:00Z" };
    expect(changeForBranch([older, change], "feat-widths")?.id).toBe("chg-1");
  });
});

describe("buildWorkRows", () => {
  it("joins the holder, the chats and the change onto one row per branch", () => {
    const rows = buildWorkRows({
      records: [record], entries: [holder], threads: [threadA, threadB], changes: [change],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].holder?.session_id).toBe("thread-a");
    expect(rows[0].threads.map((t) => t.id)).toEqual(["thread-a", "thread-b"]);
    expect(rows[0].change?.id).toBe("chg-1");
  });

  it("is a row per RECORD, so a branch with nothing on it is still listed", () => {
    const rows = buildWorkRows({ records: [record], entries: [], threads: [], changes: [] });
    expect(rows).toHaveLength(1);
    expect(rows[0].holder).toBeNull();
    expect(rows[0].change).toBeNull();
    expect(rows[0].threads).toEqual([]);
  });
});

describe("rowState", () => {
  it("is running while a live session holds the branch", () => {
    const s = rowState(row());
    expect(s.kind).toBe("running");
    expect(s.label).toBe("Held");
    expect(s.detail).toContain("operator");
  });

  it("warns when the holder is still there and nothing has been written", () => {
    const idle = { ...holder, lease: { ...lease, idle_since: "2026-09-01T11:40:00Z" } };
    expect(rowState(row({ holder: idle })).kind).toBe("warn");
  });

  it("is stopped, and says the chats are still on it, when nobody holds it", () => {
    const s = rowState(row({ holder: null }));
    expect(s.kind).toBe("stopped");
    expect(s.label).toBe("Nobody here");
    expect(s.detail).toContain("chats below are still on it");
  });

  it("is ok and reads Merged once the branch reached the trunk", () => {
    const s = rowState(row({ record: { ...record, status: "merged" } }));
    expect(s.kind).toBe("ok");
    expect(s.detail).toContain("trunk");
  });
});

describe("rowFacts", () => {
  it("is at most three: the state word, the ticket and the holder", () => {
    const r = row();
    const facts = rowFacts(r, rowState(r));
    expect(facts).toHaveLength(3);
    expect(facts[0]).toBe("Held");
    expect(facts[1]).toBe("d8676bba…");
  });

  it("gives the holder's slot to the chat count when nobody is here", () => {
    const r = row({ holder: null });
    expect(rowFacts(r, rowState(r))).toEqual(["Nobody here", "d8676bba…", "2 chats"]);
  });

  it("counts one chat in the singular", () => {
    const r = row({ holder: null, threads: [threadA] });
    expect(rowFacts(r, rowState(r))[2]).toBe("1 chat");
  });

  it("leaves the ticket out rather than printing a placeholder", () => {
    const r = row({ record: { ...record, ticket_id: null } });
    expect(rowFacts(r, rowState(r))).toEqual(["Held", holderText(r)]);
  });
});

describe("sortRows", () => {
  it("puts what needs a look above what is merely running", () => {
    const idle: DirectoryEntry = {
      ...holder, lease: { ...lease, idle_since: "2026-09-01T11:40:00Z" },
    };
    const stuck = row({ record: { ...record, id: "wt-2", branch_slug: "stuck" }, holder: idle });
    const done = row({ record: { ...record, id: "wt-3", status: "merged" }, holder: null });
    const ordered = sortRows([done, row(), stuck]);
    expect(ordered.map((r) => r.record.id)).toEqual(["wt-2", "wt-1", "wt-3"]);
  });

  it("breaks a tie on the newest tip, and does not mutate its input", () => {
    const older = row({
      record: { ...record, id: "wt-old", tip_at: "2026-08-01T00:00:00Z" }, holder: null,
    });
    const newer = row({
      record: { ...record, id: "wt-new", tip_at: "2026-08-31T00:00:00Z" }, holder: null,
    });
    const input = [older, newer];
    expect(sortRows(input).map((r) => r.record.id)).toEqual(["wt-new", "wt-old"]);
    expect(input.map((r) => r.record.id)).toEqual(["wt-old", "wt-new"]);
  });
});

describe("the lease, per thread", () => {
  it("names the thread the branch's lease is held by", () => {
    expect(leaseHolderId(row())).toBe("thread-a");
    expect(threadHoldsLease(row(), "thread-a")).toBe(true);
    expect(threadHoldsLease(row(), "thread-b")).toBe(false);
  });

  it("holds nothing once the lease has expired", () => {
    const expired = { ...holder, lease: { ...lease, state: "expired" as const } };
    expect(leaseHolderId({ holder: expired })).toBeNull();
    expect(threadHoldsLease({ holder: expired }, "thread-a")).toBe(false);
  });

  it("holds nothing when nobody is here", () => {
    expect(leaseHolderId({ holder: null })).toBeNull();
  });
});

describe("threadRole", () => {
  it("reads the role off the roster agent the thread is bound to", () => {
    expect(threadRole(threadA, agents)).toBe("maker");
  });

  it("matches by slug too, since the id a thread carries is either", () => {
    expect(threadRole({ agent_id: "reviewer" }, agents)).toBe("reviewer");
  });

  it("is null for a thread bound to nothing, which is a real answer", () => {
    expect(threadRole(threadB, agents)).toBeNull();
    expect(threadRole({ agent_id: "agent-9" }, agents)).toBeNull();
    expect(threadRole(threadA, [{ ...agents[0], role: "  " }])).toBeNull();
  });
});

describe("threadStatus", () => {
  it("marks the thread that may write", () => {
    expect(threadStatus(row(), threadA)).toEqual({ kind: "ok", label: "Holds the branch" });
  });

  it("marks the thread that is writing right now", () => {
    expect(threadStatus(row(), { ...threadA, running: true }))
      .toEqual({ kind: "running", label: "Writing" });
  });

  it("marks a running thread that does not hold the branch", () => {
    expect(threadStatus(row(), { ...threadB, running: true })?.label).toBe("Running");
  });

  it("marks nothing at all for an ordinary chat: a constant is not a signal", () => {
    expect(threadStatus(row(), threadB)).toBeNull();
  });
});

describe("sortThreads", () => {
  it("puts the lease holder first, then what is running, then by last message", () => {
    const other: WorkThread = {
      ...threadB, id: "thread-c", running: true, last_user_at: "2026-08-01T00:00:00Z",
    };
    expect(sortThreads([threadB, other, threadA], row()).map((t) => t.id))
      .toEqual(["thread-a", "thread-c", "thread-b"]);
  });

  it("orders by recency alone when nobody holds the branch", () => {
    expect(sortThreads([threadB, threadA], { holder: null }).map((t) => t.id))
      .toEqual(["thread-a", "thread-b"]);
  });
});

describe("the change on a branch", () => {
  it("carries the review vocabulary, unchanged, plus which commit into what", () => {
    const note = changeNote(change);
    expect(note).toEqual({
      kind: "warn", label: "ready to review", detail: "0f1e2d3 into main",
    });
  });

  it("is null when the branch has no change", () => {
    expect(changeNote(null)).toBeNull();
  });

  it("says a branch with no change yet is early, not broken", () => {
    expect(noChangeNote(row({ change: null }))).toContain("opens when it first has");
  });

  it("distinguishes a store that keeps no changes from a branch with none yet", () => {
    expect(noChangeNote(row({ change: null }), false)).toContain("keeps no change records");
  });

  it("says a finished branch is over rather than waiting on one", () => {
    expect(noChangeNote(row({ record: { ...record, status: "merged" }, change: null })))
      .toContain("is over");
  });
});

describe("what this view does not claim", () => {
  it("says once, in one place, why a thread carries no armed switch", () => {
    // A row must not grow a switch that writes nowhere: `chat_thread` has no
    // `armed` column yet. The note names the setting AND where it actually
    // lives, so the absence is an answer rather than a gap.
    expect(THREAD_ARMED_NOTE).toContain("armed");
    expect(THREAD_ARMED_NOTE).toContain("per scope");
  });
});

describe("the reads", () => {
  it("addresses changes by the linked project, falling back to the app id", () => {
    expect(changesAddr({ projectSlug: "workshop", appId: "a1" })).toBe("project=workshop");
    expect(changesAddr({ appId: "a1" })).toBe("app_id=a1");
    expect(changesAddr({})).toBe("");
  });

  it("escapes an address so one app cannot be served another's answer", () => {
    expect(changesAddr({ projectSlug: "a b&c" })).toBe("project=a%20b%26c");
  });

  it("keys the changes read by the address it was made with", () => {
    expect(workQueryKeys.changes("project=workshop"))
      .not.toEqual(workQueryKeys.changes("project=other"));
  });
});
