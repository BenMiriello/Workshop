// What state a branch is actually in, joined from the four things that know.
//
// THE ONE THING THIS FILE IS FOR: turning "a lease row, a live session, a
// working tree and a pending yield" into ONE honest state per branch, so that a
// stuck holder looks different from a busy one and abandoned work looks
// different from an empty tree.
//
// WHY IT IS NOT `holders.ts`. That module answers "who is here", and it starts
// from a `DirectoryEntry` -- a LIVE SESSION. It is correct and it is reused
// below for everything it covers. But the rows this view exists to surface are
// the ones where there is no live session: an expired lease over a tree that
// still has uncommitted work in it has no `DirectoryEntry` at all, so
// `holderStatus` is never called and the branch renders as "Nobody here",
// which is true and useless. Starting from the LEASE ROW and the WORKING TREE
// instead is what makes abandoned work nameable.
//
// THE LADDER RANKS LIVE BRANCHES. Above it sit the RECORD-LEVEL short-circuits
// (`Merged`, `Discarded`, and now `Gone` and `Orphaned`), and the difference is
// not cosmetic: a rung of the ladder answers "who is in this tree and is
// anything at risk", which presumes there IS a tree to be in. A row whose
// directory is missing, or whose directory git has never heard of, has no such
// answer, so it short-circuits rather than adding a rung. The ladder is still
// six deep.
//
// `presence` COMES FROM THE SERVER (`worktrees.live_branches`), which reads the
// disk and joins the record onto it. Before that join, `/branches?live=true`
// answered from the record table alone and reported eleven abandoned trees as
// none; this file could rank perfectly and still be ranking an empty list.
//
// FIVE STATUS KINDS, AND NO SIXTH (frontend/CLAUDE.md rule 4, and section 14 of
// workspace-ownership-and-leases-2026-08-22.md, which maps them for exactly
// this view: "`ok` free or yours, `running` someone working, `warn` holder
// idle, `danger` preempted or conflicted, `stopped` expired or reaped").
//
// `danger` IS USED HERE AND DELIBERATELY IS NOT IN `holders.ts`. That file
// explains why it withholds it: from a `LeaseRef` alone, a bystander session on
// a leased subject is the ordinary sweep shape and painting it red would fire
// on the healthy case every time. This module has two facts that file does not
// -- whether a yield is in flight, and whether the tree is DIRTY -- and both are
// section 14's `danger` exactly: a preempt in progress, and uncommitted work
// stranded in a tree nobody holds.
//
// THE WINDOWS ARE NOT COPIED. `idle_after_sec` and the rest arrive from
// `GET /leases` (`LeaseWindows`), because the server derives `idle_since` from
// its own constants and a second copy here would be a threshold that could
// disagree with the one that actually decided.
//
// Pure on purpose (frontend/CLAUDE.md: the target of a test is the pure rule
// behind a behaviour). Executed by branchesView.test.ts.
import type { StatusKind } from "@workshop/ui";

import type { components } from "../api/schema.gen";
import { holderName, shortId } from "./holders";
import type { BranchRecord, DirectoryEntry } from "./holders";

export type LeaseRow = components["schemas"]["LeaseRow"];
export type LeaseWindows = components["schemas"]["LeaseWindows"];
export type DriftReport = components["schemas"]["DriftReport"];
export type DriftSurface = components["schemas"]["Surface"];

/** The working tree behind one branch, as `GET /git/status` answers it.
 *  `null` while it has not been read, which is NOT the same as clean and is
 *  why the state below refuses to call anything abandoned until it knows. */
export type TreeState = { clean: boolean; entries: number } | null;

/** A branch, and everything known about who is in it. */
export type BranchRow = {
  record: BranchRecord;
  lease: LeaseRow | null;
  holder: DirectoryEntry | null;
  tree: TreeState;
  /** A preempt has asked the holder to yield and it has not let go yet. */
  yieldRequested: boolean;
  ahead: number | null;
  behind: number | null;
};

export type BranchState = {
  kind: StatusKind;
  /** The pill word. Short, because it sits in a dense row. */
  label: string;
  /** One sentence saying what the word means and what to do about it. */
  detail: string;
  /** True for the states that want the operator to look now. Used to sort and
   *  to count, never to colour -- the kind does that. */
  needsAttention: boolean;
};

// --- the state ladder ---------------------------------------------------------

/**
 * One branch's state. FIRST MATCH WINS, and the order is the point: the
 * question "is anyone's work at risk here" outranks "is anyone working here",
 * because a busy holder needs no decision and stranded work does.
 *
 * The ladder, and what each rung is FOR:
 *
 *  1. `Preempting`   a yield is in flight. `danger`, because a stop was asked
 *                    for and the holder has not answered yet.
 *  2. `Abandoned`    the tree is DIRTY and nobody holds it. `danger`, and it is
 *                    the row this whole view was built for: somebody's
 *                    uncommitted work is sitting in a tree with no owner, which
 *                    is what blocks a build and what nothing surfaced before.
 *  3. `Expired`      the lease lapsed and the tree is clean. `stopped`, a
 *                    neutral outcome: the tree is reusable, nothing is at risk.
 *  4. `Idle`         held, renewed, but no WORK event inside the idle window.
 *                    `warn`. The holder is alive and is not getting anywhere,
 *                    which is precisely "stuck" and is a reason to look rather
 *                    than a reason to act.
 *  5. `Working`      held and moving. `running`.
 *  6. `Free`         no lease, clean tree. `ok`: available, nothing owed.
 *
 * `merged` and `discarded` short-circuit above all of it, because a record that
 * is over has no live state to report and its lease row is history. So do the
 * two PRESENCE cases, for the same reason one step earlier: there is no tree to
 * hold, so there is nobody to be holding it.
 *
 *  `Gone`      a live record whose directory is not on disk. `stopped`: nothing
 *              is at risk and nothing can be lost, the bookkeeping simply
 *              outlived the tree.
 *  `Orphaned`  a directory under the branches root that git does not know
 *              about, which is what a teardown that fell back to `rmtree`
 *              leaves. `warn` and it DOES want a look: it is the one row
 *              nothing else in the system can see, it is taking up a checkout's
 *              worth of disk, and no tool but a plain delete can clear it.
 */
export function branchState(row: BranchRow, windows: LeaseWindows,
                            selfSessionId?: string | null): BranchState {
  const { record, lease, tree, yieldRequested } = row;

  if (record.status === "merged") {
    return { kind: "ok", label: "Merged", needsAttention: false,
             detail: "This branch reached the trunk. Its tree can be cleaned up." };
  }
  if (record.status === "discarded") {
    return { kind: "stopped", label: "Discarded", needsAttention: false,
             detail: "This branch was thrown away." };
  }

  if (record.presence === "gone") {
    return {
      kind: "stopped", label: "Gone", needsAttention: false,
      detail: "There is a record for this branch and no directory at the path it "
        + "names, so the tree was removed without the record being closed. "
        + "Nothing is at risk here; the row is the only thing left.",
    };
  }
  if (record.presence === "orphan") {
    return {
      kind: "warn", label: "Orphaned", needsAttention: true,
      detail: "This directory sits under the branches root and git has no worktree "
        + "and no branch for it, which is what a teardown that could not finish "
        + "leaves behind. Nothing can say what is in it, so it can only be "
        + "removed, and until it is it holds a whole checkout of disk.",
    };
  }

  const held = lease?.state === "held";
  // A DIRTY TREE IS ONLY KNOWN WHEN IT HAS BEEN READ. `tree === null` means the
  // status call has not landed (or failed), and guessing "clean" there would
  // hide exactly the rows this view exists to show, while guessing "dirty"
  // would cry abandoned at every branch for the first second of every load.
  const dirty = tree !== null && !tree.clean;

  if (held && yieldRequested) {
    return {
      kind: "danger", label: "Preempting", needsAttention: true,
      detail: `${holderLabel(row)} has been asked to stop and has not let go yet. `
        + `It gets ${windows.preempt_grace_sec} seconds, then it is stopped and its `
        + `work is snapshotted.`,
    };
  }

  if (!held && dirty) {
    return {
      kind: "danger", label: "Abandoned", needsAttention: true,
      detail: lease
        ? `Uncommitted work is sitting in this tree and its lease has expired, so `
          + `nobody owns it. Until it is committed or thrown away it can hold a `
          + `build back.`
        : `Uncommitted work is sitting in this tree and nobody holds it. Until it `
          + `is committed or thrown away it can hold a build back.`,
    };
  }

  if (lease && !held) {
    return {
      kind: "stopped", label: "Expired", needsAttention: false,
      detail: `The lease ${holderLabel(row)} held has expired and the tree is clean. `
        + `Anyone can take this branch.`,
    };
  }

  if (held && lease) {
    if (selfSessionId && lease.holder_id === selfSessionId) {
      return { kind: "ok", label: "Yours", needsAttention: false,
               detail: "You hold this branch." };
    }
    if (lease.idle_since) {
      return {
        kind: "warn", label: "Idle", needsAttention: true,
        detail: `${holderLabel(row)} still holds this and is still beating, but nothing `
          + `has been written for ${humanSeconds(windows.idle_after_sec)}. It may be `
          + `stuck.`,
      };
    }
    return { kind: "running", label: "Working", needsAttention: false,
             detail: `${holderLabel(row)} is working here.` };
  }

  return { kind: "ok", label: "Free", needsAttention: false,
           detail: "Nobody holds this branch and its tree is clean." };
}

/** Who to name in a sentence: the live session when there is one, else the
 *  lease's own holder id, else a phrase that does not pretend to know. */
export function holderLabel(row: BranchRow): string {
  if (row.holder) return holderName(row.holder);
  const id = row.lease?.holder_id;
  if (id) return `${row.lease?.holder_kind ?? "a session"} ${shortId(id)}`;
  return "Nobody";
}

// --- the numbers a row shows --------------------------------------------------

/** Seconds, in the words a row has space for. Rounds DOWN, so "2 minutes" never
 *  means 2.9 -- an operator reading a heartbeat age is deciding whether
 *  something is dead, and rounding up would make a live holder look worse than
 *  it is. */
export function humanSeconds(secs: number | null | undefined): string {
  if (secs === null || secs === undefined || Number.isNaN(secs)) return "";
  const s = Math.max(0, Math.floor(secs));
  if (s < 60) return `${s} seconds`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"}`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"}`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? "" : "s"}`;
}

/** The short form for a dense cell: `4m`, `2h`, `3d`. Empty when unknown, never
 *  a zero that would read as "just now" for a fact nobody measured. */
export function shortSeconds(secs: number | null | undefined): string {
  if (secs === null || secs === undefined || Number.isNaN(secs)) return "";
  const s = Math.max(0, Math.floor(secs));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Seconds kept as SECONDS up to two minutes, for the heartbeat sentence.
 *
 *  `humanSeconds` switches to minutes at 60, which is right for an age a reader
 *  is skimming and wrong here: the whole lease window lives under two minutes
 *  (a 30s beat, a 90s TTL), so it collapsed "75" and "90" into the same word and
 *  produced "Last heartbeat 1 minute ago; the lease lapses after 1 minute
 *  without one" -- a sentence that contrasts two numbers while printing one.
 *  Above two minutes the coarse form is better and this defers to it. */
export function beatSeconds(secs: number): string {
  const s = Math.max(0, Math.floor(secs));
  return s < 120 ? `${s} seconds` : humanSeconds(s);
}

/** How the heartbeat is doing, said only when it is worth saying.
 *
 *  A lease is renewed every `heartbeat_sec`, so an age inside a couple of beats
 *  is the healthy case and printing "beat 4s ago" on every row would be noise
 *  ("A constant is not a signal", frontend/CLAUDE.md). This returns null there
 *  and a sentence once the gap has grown past two missed beats, which is the
 *  slack `HEARTBEAT_SEC` was chosen to allow. */
export function heartbeatNote(lease: LeaseRow | null,
                              windows: LeaseWindows): string | null {
  if (!lease || lease.state !== "held") return null;
  const age = lease.since_heartbeat_sec;
  if (age === null || age === undefined) return null;
  if (age <= windows.heartbeat_sec * 2) return null;
  return `Last heartbeat ${beatSeconds(age)} ago; the lease lapses after `
    + `${beatSeconds(windows.lease_ttl_sec)} without one.`;
}

// --- what can be done about it -------------------------------------------------

/** What reaping this branch would actually do, decided once. */
export type ReapPlan = {
  /** Whether the verb may be offered at all. */
  offered: boolean;
  /** Why not, when it is not. Rendered as a sentence, never as a dead button. */
  blockedBecause: string;
  /** The consequence, named in the confirm dialog. */
  consequence: string;
  /** True when nothing can be snapshotted, so the removal is permanent and the
   *  request has to say `force` for the server to accept it. */
  force: boolean;
};

/**
 * Whether the page may offer to reap this tree, and what it must promise.
 *
 * THE PAGE FOUND THE ABANDONED TREES AND THEN HAD NOTHING TO OFFER, which is
 * half a monitoring view. The verb is `DELETE /api/worktrees/{project}/{branch}`,
 * and it is safe to offer because the server snapshots uncommitted work to
 * `refs/wip/<branch>` before it removes anything and refuses when it cannot.
 * This function's whole job is making the button's promise match that.
 *
 * THREE REFUSALS, and each is a real limit rather than caution:
 *
 *  - A HELD LEASE. Somebody is in there. Reaping a tree out from under a live
 *    holder is a preempt wearing a different name, and preempt has its own
 *    control with its own authorization.
 *  - `Gone`. There is no directory to remove. The row is a record, and closing a
 *    record is not this verb.
 *  - AN UNREAD TREE (`tree === null`). The dirty state has not landed, so the
 *    dialog cannot say what would happen to it, and a confirm that cannot name
 *    the consequence is not a confirm.
 *
 * AN ORPHAN IS OFFERED, AND IT IS THE ONE PERMANENT ONE. A directory git has no
 * metadata for cannot be snapshotted by anything, so the dialog says so plainly
 * and the request carries `force`.
 */
export function reapPlan(row: BranchRow): ReapPlan {
  const presence = row.record.presence;
  if (row.lease?.state === "held") {
    return {
      offered: false, force: false, consequence: "",
      blockedBecause: `${holderLabel(row)} holds this tree. Ask it to stop before `
        + `removing the tree it is working in.`,
    };
  }
  if (presence === "gone") {
    return {
      offered: false, force: false, consequence: "",
      blockedBecause: "There is no directory to remove. This row is a record whose "
        + "tree is already gone.",
    };
  }
  if (presence === "orphan") {
    return {
      offered: true, force: true,
      blockedBecause: "",
      consequence: "This directory has no git metadata, so nothing can record what "
        + "is in it before it goes. Removing it deletes its files permanently.",
    };
  }
  if (row.tree === null) {
    return {
      offered: false, force: false, consequence: "",
      blockedBecause: "The working tree has not been read yet, so there is no way to "
        + "say what removing it would cost. Try again in a moment.",
    };
  }
  const ahead = row.ahead ?? 0;
  const kept = `Uncommitted work is committed to refs/wip/${row.record.branch_slug} `
    + `first, so it stays recoverable in the project's repository.`;
  if (!row.tree.clean) {
    return {
      offered: true, force: false, blockedBecause: "",
      consequence: `${row.tree.entries} uncommitted `
        + `${row.tree.entries === 1 ? "entry" : "entries"} and the branch are removed. `
        + kept,
    };
  }
  if (ahead > 0) {
    return {
      offered: true, force: false, blockedBecause: "",
      consequence: `The tree is clean and the branch is ${ahead} `
        + `${ahead === 1 ? "commit" : "commits"} ahead of ${row.record.base_ref}. `
        + `The directory and the branch are removed, and the tip is kept at `
        + `refs/wip/${row.record.branch_slug} so those commits stay reachable.`,
    };
  }
  return {
    offered: true, force: false, blockedBecause: "",
    consequence: `The tree is clean and the branch has nothing the trunk does not `
      + `already have. The directory and the branch are removed.`,
  };
}

/** The reap request for one row: the path and whether it carries `force`.
 *  Here rather than at the call site so the URL and the `force` decision cannot
 *  drift apart from the sentence the dialog just showed. */
export function reapUrl(row: BranchRow, plan: ReapPlan): string {
  const p = encodeURIComponent(row.record.project_slug);
  const b = encodeURIComponent(row.record.branch_slug);
  return `/api/worktrees/${p}/${b}${plan.force ? "?force=true" : ""}`;
}

/** How old this branch's last commit is, in words, or null when nothing dated it.
 *
 *  `tip_at` is the only age an unrecorded tree has: it has no `created_at`,
 *  because nothing recorded it being created. Null rather than "unknown" so the
 *  caller can leave the cell empty; a placeholder in a dense column is noise. */
export function tipAge(record: BranchRecord, nowMs: number): string | null {
  const at = record.tip_at ?? record.created_at ?? null;
  if (!at) return null;
  const t = Date.parse(at);
  if (Number.isNaN(t)) return null;
  return shortSeconds((nowMs - t) / 1000);
}

// --- ordering -----------------------------------------------------------------

/** Rows worth looking at first.
 *
 *  Sorted by ATTENTION, not by time: this is a monitoring view, and a branch
 *  that has been abandoned for a day is more interesting than one that changed
 *  a second ago. Within a rung, the longest-standing first, because the oldest
 *  stuck thing is the one that has been costing the most.
 */
export function orderRows(rows: BranchRow[], windows: LeaseWindows,
                          selfSessionId?: string | null): BranchRow[] {
  const rank = (r: BranchRow): number => {
    const s = branchState(r, windows, selfSessionId);
    if (s.label === "Preempting") return 0;
    if (s.label === "Abandoned") return 1;
    if (s.label === "Orphaned") return 2;
    if (s.label === "Idle") return 3;
    if (s.label === "Working") return 4;
    if (s.label === "Yours") return 5;
    if (s.label === "Free") return 6;
    if (s.label === "Expired") return 7;
    if (s.label === "Gone") return 8;
    return 9;
  };
  return [...rows].sort((a, b) => {
    const d = rank(a) - rank(b);
    if (d !== 0) return d;
    const ha = a.lease?.held_for_sec ?? -1;
    const hb = b.lease?.held_for_sec ?? -1;
    return hb - ha;
  });
}

/** How many rows want a decision. Drives the page's summary line, and it counts
 *  the same `needsAttention` the ladder set, so the number and the pills can
 *  never disagree. */
export function attentionCount(rows: BranchRow[], windows: LeaseWindows,
                               selfSessionId?: string | null): number {
  return rows.filter((r) => branchState(r, windows, selfSessionId).needsAttention).length;
}

// --- drift --------------------------------------------------------------------

/** The status kind for one surface of the apply check.
 *
 *  `blocked` is `danger` and `stale` is only `warn`, and the gap between them is
 *  the whole point of showing this at all: a stale surface is fixed by running
 *  the apply step, and a blocked one is not fixed by running anything. Somebody
 *  has to commit the files. Rendering them the same colour would send the
 *  operator round the loop that cost nine hours on 2026-08-27. */
export function driftKind(state: string): StatusKind {
  if (state === "blocked") return "danger";
  if (state === "stale") return "warn";
  if (state === "undecidable") return "warn";
  return "ok";
}

export function driftLabel(state: string): string {
  if (state === "blocked") return "Blocked";
  if (state === "stale") return "Stale";
  if (state === "undecidable") return "Unknown";
  return "Current";
}

/** The one sentence the banner leads with, or null when there is nothing to say.
 *
 *  Null on a healthy installation is deliberate: this banner is only ever worth
 *  the operator's attention when something is wrong, and a permanent green bar
 *  saying "everything is current" is the constant that trains people to stop
 *  reading it. */
export function driftHeadline(report: DriftReport | null): string | null {
  if (!report) return null;
  const blocked = report.surfaces.filter((s) => s.state === "blocked");
  if (blocked.length > 0) {
    const names = blocked.map((s) => s.name).join(" and ");
    const files = blocked.reduce((n, s) => n + (s.blocking_files ?? []).length, 0);
    return `You may be looking at old code. Uncommitted work is holding the `
      + `${names} back, and rerunning the apply step will not clear it: `
      + `${files} file${files === 1 ? "" : "s"} below have to be committed first.`;
  }
  const stale = report.surfaces.filter((s) => s.state === "stale");
  if (stale.length > 0) {
    return `You may be looking at old code. The ${stale.map((s) => s.name).join(" and ")} `
      + `moved on disk and has not been applied yet.`;
  }
  if (!report.canonical) {
    return `This is a linked worktree. A build here goes to its own bundle, not to `
      + `the one the origin serves, so a change made here is invisible until the `
      + `branch merges.`;
  }
  return null;
}

/** Every file named as holding a surface back, deduplicated across surfaces.
 *  Deduplicated because a file can only be committed once, and the list is a
 *  to-do rather than a per-surface accounting. */
export function blockingFiles(report: DriftReport | null): string[] {
  if (!report) return [];
  const all = report.surfaces.flatMap((s) => s.blocking_files ?? []);
  return [...new Set(all)].sort();
}
