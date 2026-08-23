import { describe, expect, it } from "vitest";
import {
  activityLine, branchStatus, holderLine, holderName, holderStatus, idleFor,
  holderFor, leaseLine, offerableVerbs, preemptWarning, refPillLabel, shortId,
  stalenessNote, subjectLine, treeWriters, trunkLabel, writersOf,
  type BranchRecord, type DirectoryEntry, type LeaseRef,
} from "./holders";

// A live run session holding the lease on the ticket it is working. Each test
// overrides the one or two fields it is about, so a new field on the API cannot
// silently change what these assert.
const NOW = Date.parse("2026-08-23T12:00:00Z");

const lease: LeaseRef = {
  subject_kind: "ticket",
  subject_id: "d8676bba",
  holder_kind: "run",
  holder_id: "job_178748",
  principal_user_id: "019ffdf0",
  generation: 2,
  state: "held",
  idle_since: null,
  expires_at: "2026-08-23T12:01:30Z",
  acquired_at: "2026-08-23T11:50:00Z",
  last_renewed_at: "2026-08-23T11:59:48Z",
  last_work_at: "2026-08-23T11:59:48Z",
};

const entry: DirectoryEntry = {
  session_id: "job_178748",
  session_kind: "run",
  principal: "clawd-a",
  activity: "working",
  scope: "development",
  subject_kind: "ticket",
  subject_id: "d8676bba",
  subject_label: "Add a Tools settings page",
  subject_status: "doing",
  pid: 4118779,
  hostname: "bgmdebian",
  last_beat_at: "2026-08-23T11:59:48Z",
  expires_at: "2026-08-23T12:29:48Z",
  lease,
};

const reader: DirectoryEntry = {
  ...entry,
  session_id: "chat-9",
  session_kind: "chat",
  principal: null,
  activity: "",
  subject_kind: "none",
  subject_id: null,
  subject_label: null,
  subject_status: null,
  lease: null,
};

describe("holderStatus", () => {
  it("is running while a live session holds its subject", () => {
    expect(holderStatus(entry)).toEqual({ kind: "running", label: "Held" });
  });

  it("is WARN on an idle lease, and the word is still Held", () => {
    // Section 6: idle is a timestamp rendered as a warn, never a third state
    // with its own verb. The kind moves; the state word does not.
    const s = holderStatus({ ...entry, lease: { ...lease, idle_since: "2026-08-23T11:40:00Z" } });
    expect(s.kind).toBe("warn");
    expect(s.label).toBe("Held");
  });

  it("is stopped when the lease expired, which is the only other state", () => {
    expect(holderStatus({ ...entry, lease: { ...lease, state: "expired" } }))
      .toEqual({ kind: "stopped", label: "Expired" });
  });

  it("is ok when the lease is yours", () => {
    expect(holderStatus(entry, "job_178748")).toEqual({ kind: "ok", label: "Yours" });
  });

  it("is running and says only Here when there is no lease at all", () => {
    // A chat that is only reading holds nothing the lease table knows about.
    // That is not an error and it is not a fourth state.
    expect(holderStatus(reader)).toEqual({ kind: "running", label: "Here" });
  });

  it("never produces a third lease word", () => {
    const words = new Set([
      holderStatus(entry).label,
      holderStatus({ ...entry, lease: { ...lease, idle_since: "2026-08-23T11:40:00Z" } }).label,
      holderStatus({ ...entry, lease: { ...lease, state: "expired" } }).label,
    ]);
    expect([...words].sort()).toEqual(["Expired", "Held"]);
  });

  it("does not paint the sweep-and-its-job pair as a conflict", () => {
    // The sweep beats on the ticket its own job took the lease on. That is the
    // healthy shape, so it must not come back as danger.
    const sweep = { ...entry, session_id: "sweep-job_1787" };
    expect(holderStatus(sweep).kind).toBe("running");
  });
});

describe("the holder's lines", () => {
  it("names the principal and the session, because two sweeps look alike", () => {
    expect(holderName(entry)).toBe("clawd-a (run job_178748)");
    expect(holderName({ ...entry, principal: null })).toBe("run job_178748");
  });

  it("prints the channel's own activity, verbatim", () => {
    expect(activityLine(entry)).toBe("working");
    expect(activityLine(reader)).toBe("No activity reported.");
  });

  it("names the subject the read-time join resolved, and nothing when there is none", () => {
    expect(subjectLine(entry)).toBe("ticket: Add a Tools settings page (doing)");
    expect(subjectLine(reader)).toBeNull();
    expect(subjectLine({ ...entry, subject_label: null })).toBe("ticket: d8676bba (doing)");
  });

  it("states the lease in a sentence, with idle as a fact rather than a state", () => {
    expect(leaseLine(entry, NOW)).toBe("Held, and still being renewed.");
    expect(leaseLine({ ...entry, lease: { ...lease, idle_since: "2026-08-23T11:40:00Z" } }, NOW))
      .toBe("Held, but nothing has been written for 20 minutes.");
    expect(leaseLine({ ...entry, lease: { ...lease, state: "expired" } }, NOW))
      .toBe("Its lease expired. Nobody holds this now.");
    expect(leaseLine(reader, NOW)).toBe("It holds nothing.");
    expect(leaseLine({ ...reader, subject_id: "x", subject_kind: "worktree" }, NOW))
      .toBe("No lease is recorded on what it is working.");
  });

  it("says who holds it when the row is not the holder", () => {
    const sweep = { ...entry, session_id: "sweep-job_1787" };
    expect(leaseLine(sweep, NOW)).toBe("Held by job_178748, not by this session.");
  });

  it("measures idleness from the timestamp, and is null when it is not idle", () => {
    expect(idleFor(entry, NOW)).toBeNull();
    expect(idleFor({ ...entry, lease: { ...lease, idle_since: "2026-08-23T09:00:00Z" } }, NOW))
      .toBe("3 hours");
    expect(idleFor({ ...entry, lease: { ...lease, idle_since: "not a date" } }, NOW)).toBeNull();
  });

  it("shortens an id without dropping the part that identifies it", () => {
    expect(shortId("job_1787480386764_3814")).toBe("job_17874803…");
    expect(shortId("chat-9")).toBe("chat-9");
    expect(shortId(null)).toBe("");
  });
});

describe("offerableVerbs", () => {
  const ops = { selfSessionId: "chat-mine", caller: "operator" as const };

  it("puts queue first, because queue is the default", () => {
    expect(offerableVerbs(entry, ops).map((v) => v.id)).toEqual(["queue", "steer", "preempt"]);
    expect(offerableVerbs(entry, ops)[0].offered).toBe(true);
  });

  it("never promises that a steer lands now", () => {
    const steer = offerableVerbs(entry, ops).find((v) => v.id === "steer");
    expect(steer?.offered).toBe(true);
    expect(steer?.label).not.toMatch(/now|immediat/i);
    expect(steer?.help).not.toMatch(/\bnow\b|immediat|next step|next tool/i);
  });

  it("offers preempt to a person acting live, on a held lease", () => {
    const p = offerableVerbs(entry, ops).find((v) => v.id === "preempt");
    expect(p?.offered).toBe(true);
  });

  it("NEVER offers preempt to an agent, whatever the lease says", () => {
    // Section 8: an agent may never preempt another agent. It queues or asks,
    // and the shape of the offer is what makes that the easy path.
    const p = offerableVerbs(entry, { selfSessionId: "agent-7", caller: "agent" })
      .find((v) => v.id === "preempt");
    expect(p?.offered).toBe(false);
    expect(p?.reason).toContain("Queue it or ask");
    // The other two stay available: urgency has an answer that is not a stop.
    const rest = offerableVerbs(entry, { selfSessionId: "agent-7", caller: "agent" })
      .filter((v) => v.id !== "preempt");
    expect(rest.every((v) => v.offered)).toBe(true);
  });

  it("does not offer preempt when nothing is held to take", () => {
    const p = offerableVerbs({ ...entry, lease: { ...lease, state: "expired" } }, ops)
      .find((v) => v.id === "preempt");
    expect(p?.offered).toBe(false);
    expect(p?.reason).toContain("nothing to take");
  });

  it("does not offer preempt from a surface that is not a session", () => {
    // The route needs an id to hand the lease to, and a page does not have one.
    // Queue and steer are unaffected: they address the holder, not the taker.
    const verbs = offerableVerbs(entry, { selfSessionId: null, caller: "operator" });
    const p = verbs.find((v) => v.id === "preempt");
    expect(p?.offered).toBe(false);
    expect(p?.reason).toContain("nobody to hand the lease to");
    expect(verbs.filter((v) => v.id !== "preempt").every((v) => v.offered)).toBe(true);
  });

  it("offers nothing against your own session", () => {
    const verbs = offerableVerbs(entry, { selfSessionId: "job_178748", caller: "operator" });
    expect(verbs.every((v) => !v.offered)).toBe(true);
    expect(verbs.every((v) => v.reason === "This is your own session.")).toBe(true);
  });
});

describe("preemptWarning", () => {
  it("says what happens to the holder's work, in the words that are true of it", () => {
    const w = preemptWarning(entry);
    expect(w).toContain("snapshotted");
    expect(w).toContain("not committed");
    expect(w).toContain("the branch is not moved");
    expect(w).toContain("Add a Tools settings page");
  });
});

describe("the ref", () => {
  const rec: BranchRecord = {
    id: "wt-1",
    app_id: "app-1",
    project_slug: "atlas",
    scope: "project:atlas",
    branch_slug: "t-91ac",
    branch: "task/t-91ac",
    path: "/srv/branches/atlas/t-91ac",
    base_ref: "origin/main",
    base_sha: "abc123",
    ticket_id: "t-91ac",
    allocated_by: "sweep-1",
    status: "provisioned",
    merged_sha: null,
    removed_at: null,
    created_at: "2026-08-23T10:00:00Z",
    updated_at: "2026-08-23T10:00:00Z",
  };

  it("prefers the project's own record of the default branch", () => {
    expect(trunkLabel([rec], "trunk")).toBe("trunk");
  });

  it("falls back to what the branches were cut from, without the remote prefix", () => {
    expect(trunkLabel([rec])).toBe("main");
    expect(trunkLabel([rec, { ...rec, id: "wt-2", base_ref: "develop" },
                       { ...rec, id: "wt-3", base_ref: "develop" }])).toBe("develop");
  });

  it("returns null rather than guessing 'main' at a repo it has never read", () => {
    expect(trunkLabel([])).toBeNull();
    expect(refPillLabel(trunkLabel([]))).toBe("no ref recorded");
    expect(refPillLabel("main")).toBe("main");
  });

  it("reads a branch nobody is on as stopped, not as a problem", () => {
    expect(branchStatus(rec, null)).toEqual({ kind: "stopped", label: "Nobody here" });
  });

  it("takes a live branch's status from whoever is present on it", () => {
    expect(branchStatus(rec, entry)).toEqual({ kind: "running", label: "Held" });
  });

  it("reads a terminal record from the record, since nobody is on it by then", () => {
    expect(branchStatus({ ...rec, status: "merged" }, null).label).toBe("Merged");
    expect(branchStatus({ ...rec, status: "discarded" }, null).kind).toBe("stopped");
  });
});

describe("holderFor", () => {
  const job = { ...entry, subject_kind: "worktree", subject_id: "wt-1",
    lease: { ...lease, subject_kind: "worktree", subject_id: "wt-1" } };
  const sweep = { ...job, session_id: "sweep-1" };

  it("names the session the lease names, not the one beating beside it", () => {
    expect(holderFor([sweep, job], "wt-1")?.session_id).toBe("job_178748");
  });

  it("falls back to whoever is present when no lease row matches", () => {
    expect(holderFor([{ ...sweep, lease: null }], "wt-1")?.session_id).toBe("sweep-1");
  });

  it("is null for a subject nobody is on", () => {
    expect(holderFor([job], "wt-2")).toBeNull();
    expect(holderFor([job], null)).toBeNull();
  });
});

describe("reader staleness", () => {
  const wt: DirectoryEntry = {
    ...entry,
    subject_kind: "worktree",
    subject_id: "wt-1",
    subject_label: "task/t-91ac",
    lease: { ...lease, subject_kind: "worktree", subject_id: "wt-1" },
  };

  it("finds the sessions holding a lease on what this surface reads", () => {
    expect(writersOf([wt, reader], ["wt-1"]).map((e) => e.session_id)).toEqual(["job_178748"]);
  });

  it("does not count you as writing over yourself", () => {
    expect(writersOf([wt], ["wt-1"], "job_178748")).toEqual([]);
  });

  it("does not count a session whose lease has expired", () => {
    const gone = { ...wt, lease: { ...wt.lease!, state: "expired" } };
    expect(writersOf([gone], ["wt-1"])).toEqual([]);
  });

  it("counts a session working this scope, whose tree has no branch record", () => {
    // The common case: a sweep holding a TICKET lease in project:atlas is
    // writing that project's trunk, and there is no worktree row to match on.
    const sweep = { ...entry, scope: "project:atlas" };
    expect(treeWriters([sweep], { scope: "project:atlas" }).map((e) => e.session_id))
      .toEqual(["job_178748"]);
    expect(treeWriters([sweep], { scope: "project:other" })).toEqual([]);
  });

  it("counts a session on one of this app's branch trees", () => {
    expect(treeWriters([wt], { subjectIds: ["wt-1"] }).length).toBe(1);
  });

  it("leaves you out, and leaves an expired lease out", () => {
    expect(treeWriters([wt], { subjectIds: ["wt-1"], selfSessionId: "job_178748" })).toEqual([]);
    const gone = { ...wt, lease: { ...wt.lease!, state: "expired" } };
    expect(treeWriters([gone], { subjectIds: ["wt-1"], scope: "development" })).toEqual([]);
  });

  it("says nothing when nobody else is writing", () => {
    expect(stalenessNote([])).toBeNull();
  });

  it("says it is stale and does NOT claim it can fix it", () => {
    // Section 11: no invalidation protocol fixes a read already in a context
    // window. The note indicates; the remedy is a message into the session.
    const note = stalenessNote([wt])!;
    expect(note).toContain("may already be out of date");
    expect(note).toContain("has to be told");
    expect(note).not.toMatch(/invalidat|refresh(ed|es)? automatically/i);
  });

  it("counts them when there is more than one, and agrees with itself", () => {
    const note = stalenessNote([wt, { ...wt, session_id: "other" }])!;
    expect(note).toContain("2 sessions are writing");
  });
});

describe("holderLine", () => {
  it("is one line: who, then what it is doing", () => {
    expect(holderLine(entry)).toBe("clawd-a (run job_178748), working");
  });

  it("does not trail a comma when the channel reported no activity", () => {
    expect(holderLine(reader)).toBe("chat chat-9");
  });
});
