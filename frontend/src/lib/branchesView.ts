// What state a branch is actually in, joined from the four things that know.
//
// THE ONE THING THIS FILE IS FOR: turning "a lease row, a live session, a
// working tree and a pending yield" into ONE honest state per branch, in words
// an operator does not have to learn.
//
// FIVE WORDS, AND THEY ANSWER THREE QUESTIONS. The operator asked exactly three
// things of this page: is somebody working in this tree, is there work in it
// that would be lost, and is it finished with. So there are four states a live
// tree can be in and one for a branch that is over:
//
//   In use        somebody is working here          leave it alone
//   Stuck         somebody holds it, nothing moves  look at the holder
//   Unsaved work  nobody holds it, changes inside   save it or reap it
//   Free          nobody holds it, nothing to lose  take it or reap it
//   Done          the branch is over                nothing
//
// It used to say Merged, Discarded, Gone, Orphaned, Dormant, Abandoned,
// Expired, Idle, Working, Yours, Free and Preempting -- twelve words, three of
// them ("dormant", "abandoned", "orphaned") different shades of "nobody is
// here" that no reader could tell apart or act on differently. The rule that
// replaced them: if two rows lead to the same decision they are one state, and
// no word may need a Workshop-specific meaning to read.
//
// THE WORD IS THE STATE; THE COLOUR IS THE URGENCY. `holders.ts` already works
// this way and says why -- "a held lease that has gone quiet is `warn` and it
// still says Held. The state did not change, so the word does not; what changed
// is that nothing has been written for ten minutes." So `Stuck` is `warn` for an
// idle holder and `danger` once a stop has been asked for and refused, and
// `Unsaved work` is `warn` while it is news and `stopped` once it is inventory.
// That is also what keeps a colour from going constant: eleven identical red
// rows was the bug, and eleven rows that are all the same word but not all the
// same colour is the fix.
//
// WHY IT IS NOT `holders.ts`. That module answers "who is here", and it starts
// from a `DirectoryEntry` -- a LIVE SESSION. It is correct and it is reused
// below for everything it covers. But the rows this view exists to surface are
// the ones where there is no live session: an expired lease over a tree that
// still has uncommitted work in it has no `DirectoryEntry` at all, so
// `holderStatus` is never called and the branch renders as "Nobody here",
// which is true and useless. Starting from the LEASE ROW and the WORKING TREE
// instead is what makes stranded work nameable.
//
// `presence` COMES FROM THE SERVER (`worktrees.live_branches`), which reads the
// disk and joins the record onto it. Before that join, `/branches?live=true`
// answered from the record table alone and reported eleven stranded trees as
// none; this file could rank perfectly and still be ranking an empty list.
//
// FIVE STATUS KINDS, AND NO SIXTH (frontend/CLAUDE.md rule 4, and section 14 of
// workspace-ownership-and-leases-2026-08-22.md, which maps them for exactly
// this view: "`ok` free or yours, `running` someone working, `warn` holder
// idle, `danger` preempted or conflicted, `stopped` expired or reaped").
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

/** The working tree behind one branch, as `GET /git/status` answers it.
 *  `null` while it has not been read, which is NOT the same as clean and is
 *  why the state below refuses to name unsaved work until it knows. */
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

/** When a tree nobody holds stops being news and becomes disk.
 *
 *  NOT A LEASE WINDOW, which is why it is here and not read from `LeaseWindows`.
 *  The server's thresholds decide who holds what and when a claim lapses; this
 *  decides how loudly one row is drawn, which is a presentation call and has no
 *  counterpart on the server to disagree with. A day is the span over which
 *  stranded work is still somebody's afternoon: past it, nobody is coming back
 *  to it this session. */
export const SETTLED_AFTER_SEC = 24 * 60 * 60;

/** How wide a run of hex has to be before it is an ID rather than a name, and
 *  how much of it a row shows. Eight hex characters is the length git itself
 *  picked for an abbreviated object, for the same reason: it identifies without
 *  being read. */
const HEX_ID_MIN = 24;
const HEX_ID_KEEP = 8;

/**
 * A branch name in the width a row has.
 *
 * A 32-CHARACTER HEX SLUG IS NOT A NAME, and rendered in full it wrapped over
 * two lines and took most of the page's vertical space while identifying
 * nothing a prefix would not. This shortens ONLY the case that is machine
 * output: an optional `t-` ticket prefix followed by a long run of hex. A slug
 * somebody chose (`t-smoke2`, `feat-widths`) is short already and passes
 * through whole, because abbreviating a real name is how you make a page
 * unreadable in the other direction.
 *
 * The ellipsis is not decoration: it is the row promising that there is more,
 * so a reader never mistakes the prefix for the whole value. The full slug is
 * on the row's own panel, where the reap dialog and the copy button need it.
 */
export function branchLabel(
  row: string | { branch_slug: string; display_name?: string | null },
): string {
  if (typeof row !== "string" && row.display_name) return row.display_name;
  const slug = typeof row === "string" ? row : row.branch_slug;
  const m = /^(t-)?([0-9a-f]+)$/i.exec(slug.trim());
  if (!m) return slug;
  const [, prefix = "", hex] = m;
  if (hex.length < HEX_ID_MIN) return slug;
  return `${prefix}${hex.slice(0, HEX_ID_KEEP)}…`;
}

/**
 * One branch's state. FIRST MATCH WINS, and the order is the point: the
 * question "is anything here at risk" outranks "is anyone working here",
 * because a busy holder needs no decision and stranded work does.
 *
 * The five words are in this file's header. What each one folds, and why the
 * fold is safe (the test is whether the two rows want DIFFERENT decisions):
 *
 *  `Done`          merged, discarded, and a record whose directory is not on
 *                  disk. Three ways for a branch to be over; the decision in
 *                  all three is "nothing", so they are one word and three
 *                  sentences. `stopped`.
 *  `Stuck`         a holder that has been asked to stop and has not let go
 *                  (`danger`), and a holder that is alive and writing nothing
 *                  (`warn`). Both mean: open the holder and decide about it.
 *  `Unsaved work`  a dirty tree nobody holds, and a directory under the
 *                  branches root that git has no branch for. The second cannot
 *                  even be read, so it is the same decision at its worst: do
 *                  not delete this until you know what is in it. `warn` while
 *                  the tree is recent, `stopped` once it has sat a day, because
 *                  eleven of these at rest is inventory rather than an alarm.
 *  `In use`        held and moving, yours or anyone's. `running`.
 *  `Free`          no live hold and nothing uncommitted, whether the lease
 *                  lapsed or was never taken. `ok`: take it or reap it.
 */
export function branchState(row: BranchRow, windows: LeaseWindows,
                            selfSessionId?: string | null,
                            nowMs: number = Date.now()): BranchState {
  const { record, lease, tree, yieldRequested } = row;

  if (record.status === "merged") {
    return { kind: "stopped", label: "Done", needsAttention: false,
             detail: "This branch reached the trunk. Its tree can be cleaned up." };
  }
  if (record.status === "discarded") {
    return { kind: "stopped", label: "Done", needsAttention: false,
             detail: "This branch was thrown away." };
  }
  if (record.presence === "gone") {
    return {
      kind: "stopped", label: "Done", needsAttention: false,
      detail: "The record is still here and the directory is not. Nothing is at "
        + "risk; this row is all that is left of it.",
    };
  }

  const held = lease?.state === "held";
  // A DIRTY TREE IS ONLY KNOWN WHEN IT HAS BEEN READ. `tree === null` means the
  // status call has not landed (or failed), and guessing "clean" there would
  // hide exactly the rows this view exists to show, while guessing "dirty"
  // would cry unsaved work at every branch for the first second of every load.
  const dirty = tree !== null && !tree.clean;

  if (held && yieldRequested) {
    return {
      kind: "danger", label: "Stuck", needsAttention: true,
      detail: `${holderLabel(row)} was asked to stop and has not let go. It gets `
        + `${windows.preempt_grace_sec} seconds, then it is stopped and its work is `
        + `snapshotted.`,
    };
  }

  if (record.presence === "orphan") {
    return {
      kind: "warn", label: "Unsaved work", needsAttention: true,
      detail: "Git has no worktree and no branch for this directory, so nothing "
        + "can say what is in it. Removing it is the only option and it is "
        + "permanent. Until then it holds a whole checkout of disk.",
    };
  }

  if (!held && dirty) {
    // A HUE THAT NEVER VARIES CARRIES NO INFORMATION (frontend/CLAUDE.md, "a
    // constant is not a signal"). Eleven dirty trees nobody holds is the
    // RESTING STATE of this system, not an emergency, and painting every one of
    // them loud produced a screen of identical red that says nothing about
    // which row to look at. Same word, quieter colour: work stranded in the
    // last day is news, and a tree that has sat for a week is inventory. The
    // age is printed on the row beside it, so the two shades are readable.
    const age = tipAgeSeconds(record, nowMs);
    if (age !== null && age >= SETTLED_AFTER_SEC) {
      return {
        kind: "stopped", label: "Unsaved work", needsAttention: false,
        detail: `Work here was never committed and nobody holds it. Nothing has `
          + `been committed for ${humanSeconds(age)}, so this is disk rather than `
          + `an emergency.`,
      };
    }
    return {
      kind: "warn", label: "Unsaved work", needsAttention: true,
      detail: lease
        ? `Work here was never committed and the lease on it has expired, so `
          + `nobody owns it. Commit it or reap it; until then it can hold a build `
          + `back.`
        : `Work here was never committed and nobody holds it. Commit it or reap `
          + `it; until then it can hold a build back.`,
    };
  }

  if (held && lease) {
    if (selfSessionId && lease.holder_id === selfSessionId) {
      return { kind: "running", label: "In use", needsAttention: false,
               detail: "You hold this branch." };
    }
    if (lease.idle_since) {
      return {
        kind: "warn", label: "Stuck", needsAttention: true,
        detail: `${holderLabel(row)} still holds this and is still beating, but nothing `
          + `has been written for ${humanSeconds(windows.idle_after_sec)}. It may be `
          + `stuck.`,
      };
    }
    return { kind: "running", label: "In use", needsAttention: false,
             detail: `${holderLabel(row)} is working here.` };
  }

  if (lease) {
    return {
      kind: "ok", label: "Free", needsAttention: false,
      detail: `The lease ${holderLabel(row)} held has expired and the tree is `
        + `clean. Anyone can take this branch.`,
    };
  }

  return { kind: "ok", label: "Free", needsAttention: false,
           detail: "Nobody holds this branch and its tree is clean." };
}

/**
 * The way from a tree to the thing that is working in it, or null.
 *
 * THE ROW LINKS TO THE CHAT, and that is the single most useful thing this page
 * does (operator, 2026-08-28: "the main way we'll find worktrees is through
 * chats or agents"). A lease's holder is a session, and `chat_worktree.enter`
 * takes it under `holder_kind="chat"` with the THREAD ID as the holder id, so
 * the conversation is addressable from the lease row alone. `run_worktree` does
 * the same with a job id, which `/runs/:id` resolves.
 *
 * The live directory entry is preferred over the lease row for one reason: it
 * carries the session's own `scope`, which is the chat's context key. The lease
 * table does not, so for an expired lease the branch record's scope is used and
 * the chat route corrects itself if that guess is wrong (`FocusedChat` re-reads
 * the thread and replaces the URL).
 *
 * Null for every other holder kind rather than a guess: a link that lands on a
 * 404 is worse than a name with no link, and the panel names the holder either
 * way.
 */
export type HolderLink = { to: string; label: string };

export function holderLink(row: BranchRow): HolderLink | null {
  const entry = row.holder;
  const lease = row.lease;
  const kind = entry?.session_kind || lease?.holder_kind || "";
  const id = entry?.session_id || lease?.holder_id || "";
  if (!id) return null;
  if (kind === "chat") {
    const scope = (entry?.scope || row.record.scope || "general").trim() || "general";
    return {
      to: `/chat/${encodeURIComponent(scope)}?thread=${encodeURIComponent(id)}`,
      label: "Open the chat working here",
    };
  }
  if (kind === "run") {
    return { to: `/runs/${encodeURIComponent(id)}`, label: "Open the run working here" };
  }
  return null;
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

/**
 * The facts one row prints beside a branch name, in reading order.
 *
 * ONE TREE IS ONE ROW ON A 390px SCREEN, which is what this list is for and
 * what the eight-column table it replaces could not be. A table with columns
 * running off the right edge is the wrong primitive on a phone (the kit says so
 * where `.ws-listrow` is defined), so the columns that were being clipped are
 * not squeezed, they are CUT: what is left is the state word, the one number
 * that varies between rows in that state, and the age. Everything else -- the
 * holder's id, how long it has been held, the heartbeat, the drift from base,
 * the path -- moves to the row's own panel, which opens under it.
 *
 * The state WORD is here rather than only in the dot, for the reason
 * frontend/CLAUDE.md gives about ticket lists: a colour answers "is this stuck"
 * only for a reader who has already learned the five hues.
 */
export function rowFacts(row: BranchRow, state: BranchState): string[] {
  const facts = [state.label];
  if (row.lease?.state === "held") {
    facts.push(holderLabel(row));
  } else if (row.tree && !row.tree.clean) {
    // How much is stranded. Said as a COUNT OF FILES, not "N uncommitted": the
    // state word beside it is already "Unsaved work", and a row that reads
    // "Unsaved work / 2 uncommitted" spends its second fact restating its
    // first.
    facts.push(`${row.tree.entries} ${row.tree.entries === 1 ? "file" : "files"}`);
  }
  return facts;
}


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
 * THE PAGE FOUND THE STRANDED TREES AND THEN HAD NOTHING TO OFFER, which is
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
 *  - A RECORD WITH NO DIRECTORY (`Done`). There is nothing to remove. The row
 *    is bookkeeping, and closing a record is not this verb.
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
  const secs = tipAgeSeconds(record, nowMs);
  return secs === null ? null : shortSeconds(secs);
}

/** The same age as a number, for the ladder.
 *
 *  Null rather than zero when nothing dated the branch, and the ladder treats
 *  null as "not old" rather than "old": an age nobody recorded is not evidence
 *  that a tree has been sitting, and quieting a row on a fact that was never
 *  measured is the one way this rung could hide real stranded work. */
export function tipAgeSeconds(record: BranchRecord, nowMs: number): number | null {
  const at = record.tip_at ?? record.created_at ?? null;
  if (!at) return null;
  const t = Date.parse(at);
  if (Number.isNaN(t)) return null;
  return Math.max(0, (nowMs - t) / 1000);
}

// --- ordering -----------------------------------------------------------------

/** Rows worth looking at first.
 *
 *  Sorted by ATTENTION, not by time: this is a monitoring view, and a branch
 *  whose work has been stranded for a day is more interesting than one that changed
 *  a second ago. Within a rung, the longest-standing first, because the oldest
 *  stuck thing is the one that has been costing the most.
 */
export function orderRows(rows: BranchRow[], windows: LeaseWindows,
                          selfSessionId?: string | null,
                          nowMs: number = Date.now()): BranchRow[] {
  // RANKED BY THE KIND, NOT BY THE WORD, so the order and the colour cannot
  // disagree: whatever is painted loudest is at the top. The one split inside a
  // kind is at the bottom, where a tree still holding uncommitted work sorts
  // above two that hold none.
  const rank = (r: BranchRow): number => {
    const s = branchState(r, windows, selfSessionId, nowMs);
    if (s.kind === "danger") return 0;
    if (s.kind === "warn") return 1;
    if (s.kind === "running") return 2;
    if (s.kind === "ok") return 3;
    return s.label === "Unsaved work" ? 4 : 5;
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
                               selfSessionId?: string | null,
                               nowMs: number = Date.now()): number {
  return rows.filter(
    (r) => branchState(r, windows, selfSessionId, nowMs).needsAttention).length;
}
