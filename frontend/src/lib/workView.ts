// The app's Work view: a branch is a row, and everything known about it joined
// into one shape.
//
// THE ONE THING THIS FILE IS FOR: turning four independent reads (the branch
// records, who is live, the app's chats, the app's changes) into one row per
// branch, and deciding what that row says collapsed and expanded. The .tsx
// beside it renders what this decided and invents nothing.
//
// Design: threads-branches-and-pages-2026-08-29.md section 6, "Per app". A
// branch is a row; collapsed it shows the name, the ticket and the holder;
// expanded it shows the threads on it and the state of its change. Section 3 is
// why the word here is BRANCH and never "worktree": one branch has exactly one
// worktree, and the directory it lives in is a detail, not a name.
//
// WHAT IS REUSED RATHER THAN REBUILT. `holders.ts` already answers "who holds
// this subject and what colour is that" (`branchStatus`, `holderFor`,
// `holderName`) and `changeReview.ts` already answers "what state is this change
// in" (`changeStatusKind`, `changeStatusLabel`). Both are imported. What is new
// here is only the JOIN and the ordering, because nothing else in the tree had a
// reason to put chats, changes and branch records in one row.
//
// WHAT THIS DELIBERATELY DOES NOT CLAIM. Section 1 of the design gives a thread
// two settings, `armed` and `role`. Neither is on the wire yet (section 7 step 1
// has not landed: no `chat_thread.armed` column, nothing in the generated schema
// carries it), so no function here returns an armed state. A ROLE is derivable
// today, but only for a thread bound to a roster agent, so `threadRole` reads
// the join it actually has and answers null for everything else. Null is a
// perfectly good value for a role (design section 1) and a guess would not be.
//
// Pure on purpose (frontend/CLAUDE.md: the target of a test is the pure rule
// behind a behaviour; this repo has no DOM harness). Executed by
// workView.test.ts.
import type { StatusKind } from "@workshop/ui";

import type { components } from "../api/schema.gen";
import { branchStatus, holderFor, holderName, shortId } from "./holders";
import type { BranchRecord, DirectoryEntry } from "./holders";
import { changeStatusKind, changeStatusLabel, isOpenChange } from "./changeReview";
import type { Change } from "./changeReview";
import { shortSha } from "./sha";

/** One conversation bound to this app, as `GET /api/apps/{id}/chats` answers
 *  it. The same row the Chats tab lists; this view groups them by the branch
 *  each one is on. */
export type WorkThread = components["schemas"]["AppChat"];

/** One roster agent linked to this app, as `GET /api/apps/{id}/agents` answers
 *  it.
 *
 *  This was four hand-written fields under a comment saying the route declared
 *  no response model. It does now: the route answers `AppAgentList`, whose rows
 *  are `RosterAgent`, so the wire contract is nameable and the copy is not a
 *  contract but a guess with a longer life than the reason for it. */
export type WorkAgent = components["schemas"]["RosterAgent"];

/** A branch, and everything on it. */
export type WorkRow = {
  record: BranchRecord;
  /** The live session holding this branch, or null when nobody is here. */
  holder: DirectoryEntry | null;
  /** The conversations whose current branch this is, ordered by `sortThreads`. */
  threads: WorkThread[];
  /** The change this branch has opened, or null. */
  change: Change | null;
};

export type WorkState = {
  kind: StatusKind;
  /** The pill word. Short: it sits in a dense row on a 390px screen. */
  label: string;
  /** One sentence saying what the word means. Shown on the open row. */
  detail: string;
};

// --- naming ------------------------------------------------------------------

/** How wide a run of hex has to be before it is an id rather than a name, and
 *  how much of it a row shows. Eight is the length git itself picked for an
 *  abbreviated object, for the same reason: it identifies without being read. */
const HEX_ID_MIN = 24;
const HEX_ID_KEEP = 8;

/**
 * A branch name in the width a row has.
 *
 * `display_name` is the repo's own name for the branch and wins whenever it is
 * there (design section 3: display names were being dropped on the wire, which
 * is why branches showed as hex). Otherwise this shortens ONLY the case that is
 * machine output: an optional `t-` ticket prefix followed by a long run of hex.
 * A slug somebody chose passes through whole, because abbreviating a real name
 * is how a list becomes unreadable in the other direction.
 */
export function branchName(
  record: Pick<BranchRecord, "branch_slug" | "display_name">,
): string {
  if (record.display_name) return record.display_name;
  const slug = (record.branch_slug ?? "").trim();
  const m = /^(t-)?([0-9a-f]+)$/i.exec(slug);
  if (!m) return slug;
  const [, prefix = "", hex] = m;
  if (hex.length < HEX_ID_MIN) return slug;
  return `${prefix}${hex.slice(0, HEX_ID_KEEP)}…`;
}

/** The ticket this branch was opened for, abbreviated, or null. Null rather
 *  than "none": an absent fact takes no room in a meta line. */
export function ticketLabel(record: Pick<BranchRecord, "ticket_id">): string | null {
  const id = (record.ticket_id ?? "").trim();
  return id ? shortId(id, HEX_ID_KEEP) : null;
}

/** Who is on this branch, in words. "Nobody" rather than an empty string, so a
 *  caller can print it without a branch of its own. */
export function holderText(row: Pick<WorkRow, "holder">): string {
  return row.holder ? holderName(row.holder) : "Nobody";
}

// --- the join ----------------------------------------------------------------

/**
 * Which of this app's chats are on which branch.
 *
 * Keyed by `branch_slug` because that is the only link a thread carries
 * (`ThreadSummary.branch_slug`), and a thread naming a branch this app has no
 * record for is dropped rather than given a row of its own: this view lists
 * BRANCHES, and a row with no record could say nothing about the change or the
 * lease. A thread with no branch at all is ordinary (design addendum: "a thread
 * with no branch is ordinary, not broken") and belongs on Chats.
 */
export function threadsByBranch(threads: WorkThread[]): Map<string, WorkThread[]> {
  const out = new Map<string, WorkThread[]>();
  for (const t of threads) {
    const slug = (t.branch_slug ?? "").trim();
    if (!slug) continue;
    const rows = out.get(slug);
    if (rows) rows.push(t);
    else out.set(slug, [t]);
  }
  return out;
}

/**
 * The change on one branch, or null.
 *
 * An OPEN change wins over a settled one, and among equals the most recently
 * updated. A branch that was merged and then cut again carries two rows, and
 * the one still in play is the one the operator can act on.
 */
export function changeForBranch(changes: Change[], branchSlug: string): Change | null {
  const mine = changes.filter((c) => c.branch_slug === branchSlug);
  if (mine.length === 0) return null;
  const at = (c: Change): number => Date.parse(c.updated_at ?? c.created_at ?? "") || 0;
  return [...mine].sort((a, b) => {
    const open = Number(isOpenChange(b)) - Number(isOpenChange(a));
    return open !== 0 ? open : at(b) - at(a);
  })[0];
}

/** One row per branch record, with its holder, its threads and its change. */
export function buildWorkRows(
  { records, entries, threads, changes }: {
    records: BranchRecord[];
    entries: DirectoryEntry[];
    threads: WorkThread[];
    changes: Change[];
  },
): WorkRow[] {
  const byBranch = threadsByBranch(threads);
  return records.map((record) => {
    const row: WorkRow = {
      record,
      holder: holderFor(entries, record.id),
      threads: [],
      change: changeForBranch(changes, record.branch_slug),
    };
    row.threads = sortThreads(byBranch.get(record.branch_slug) ?? [], row);
    return row;
  });
}

// --- the row's state ---------------------------------------------------------

/**
 * What state one branch is in, in the five kinds and one word.
 *
 * The WORD comes from `holders.branchStatus`, which is already the tree's
 * answer to "who holds this subject", already tested, and already the word the
 * ref pill prints for the same branch. Rendering a second vocabulary here is
 * exactly how the retired branches page ended up with twelve state words for
 * five decisions. What this adds is the SENTENCE, which a collapsed row has no
 * room for and an open one does.
 */
export function rowState(row: WorkRow, selfSessionId?: string | null): WorkState {
  const status = branchStatus(row.record, row.holder, selfSessionId);
  return { ...status, detail: rowDetail(row, status.label) };
}

function rowDetail(row: WorkRow, label: string): string {
  switch (label) {
    case "Merged":
      return "This branch reached the trunk.";
    case "Discarded":
      return "This branch was thrown away.";
    case "Nobody here":
      return row.threads.length > 0
        ? "No session holds this branch. The chats below are still on it."
        : "No session holds this branch and no chat is on it.";
    case "Expired":
      return `The lease ${holderText(row)} held has expired, so nobody owns this branch now.`;
    case "Yours":
      return "You hold this branch.";
    case "Held":
      return `${holderText(row)} holds this branch.`;
    default:
      return `${holderText(row)} is here.`;
  }
}

/**
 * The facts a COLLAPSED row prints beside the branch name, in reading order.
 *
 * Section 6 names three: the name, the ticket and the holder. The name is the
 * title, so this is the other two plus the state word, which goes in the meta
 * line as plain text for the reason frontend/CLAUDE.md gives about ticket
 * lists: a colour answers "is anyone on this" only for a reader who has already
 * learned the five hues.
 *
 * Capped at three because `.ws-listrow__meta` does not wrap and does not scroll:
 * a fourth fact is not shown small, it is clipped. So the holder's name gives
 * way to the thread count only when there is no holder, which is the case where
 * the state word has already said "Nobody here" and the useful remaining fact is
 * how many conversations are still pointed at this branch.
 */
export function rowFacts(row: WorkRow, state: WorkState): string[] {
  const facts = [state.label];
  const ticket = ticketLabel(row.record);
  if (ticket) facts.push(ticket);
  if (row.holder) facts.push(holderName(row.holder));
  else if (row.threads.length > 0) {
    facts.push(`${row.threads.length} ${row.threads.length === 1 ? "chat" : "chats"}`);
  }
  return facts;
}

// --- ordering ----------------------------------------------------------------

// Ranked by the KIND, not by the word, so the order and the colour cannot
// disagree: whatever is painted loudest sits at the top.
const KIND_RANK: Record<StatusKind, number> = {
  danger: 0,
  warn: 1,
  running: 2,
  ok: 3,
  stopped: 4,
};

/**
 * The branches worth looking at first.
 *
 * Not by time: this is a work view, and a branch whose holder has gone quiet is
 * more interesting than one that moved a second ago. Within a rung, the branch
 * whose tip is newest first, because that is the work in progress.
 */
export function sortRows(
  rows: WorkRow[], selfSessionId?: string | null,
): WorkRow[] {
  const at = (r: WorkRow): number =>
    Date.parse(r.record.tip_at ?? r.record.created_at ?? "") || 0;
  return [...rows].sort((a, b) => {
    const d = KIND_RANK[rowState(a, selfSessionId).kind] - KIND_RANK[rowState(b, selfSessionId).kind];
    return d !== 0 ? d : at(b) - at(a);
  });
}

// --- the threads on a branch -------------------------------------------------

/** The thread id the branch's lease names, or null. A chat takes a worktree
 *  lease under its THREAD id (`chat_worktree.enter`, `holder_kind="chat"`), so
 *  this is what makes "which of these threads may write" answerable at all. */
export function leaseHolderId(row: Pick<WorkRow, "holder">): string | null {
  const lease = row.holder?.lease;
  if (!lease || lease.state !== "held") return null;
  return lease.holder_id ?? null;
}

/** Whether this thread is the one that may write to this branch. */
export function threadHoldsLease(
  row: Pick<WorkRow, "holder">, threadId: string,
): boolean {
  return !!threadId && leaseHolderId(row) === threadId;
}

/**
 * The role this thread is working under, or null.
 *
 * DERIVED FROM THE ONE JOIN THAT EXISTS. A thread carries `agent_id` and a
 * roster agent carries `role`, so a thread bound to an agent has a role today.
 * A thread bound to nothing has none, and that is a real answer rather than a
 * missing one (design section 1: "Null is a perfectly good value").
 */
export function threadRole(
  thread: Pick<WorkThread, "agent_id">, agents: WorkAgent[],
): string | null {
  const id = (thread.agent_id ?? "").trim();
  if (!id) return null;
  const agent = agents.find((a) => a.id === id || a.slug === id);
  const role = (agent?.role ?? "").trim();
  return role || null;
}

/**
 * What one thread row's status mark says, or null for nothing worth marking.
 *
 * MARK THE EXCEPTION, NOT THE DEFAULT (frontend/CLAUDE.md). An ordinary chat
 * sitting on a branch is the resting state of every one of these lists, so it
 * gets no pill; a pill on every row is a constant, and a constant is not a
 * signal. The two exceptions are the two facts this view exists to surface:
 * this thread may write, and this thread is writing right now.
 */
export function threadStatus(
  row: Pick<WorkRow, "holder">, thread: Pick<WorkThread, "id" | "running">,
): { kind: StatusKind; label: string } | null {
  const holds = threadHoldsLease(row, thread.id);
  if (holds && thread.running) return { kind: "running", label: "Writing" };
  if (holds) return { kind: "ok", label: "Holds the branch" };
  if (thread.running) return { kind: "running", label: "Running" };
  return null;
}

/** The thread that may write first, then whatever is running, then by when the
 *  operator last spoke, which is what every other chat list here orders by. */
export function sortThreads(
  threads: WorkThread[], row: Pick<WorkRow, "holder">,
): WorkThread[] {
  const at = (t: WorkThread): number =>
    Date.parse(t.last_user_at ?? t.updated_at ?? t.created_at ?? "") || 0;
  const rank = (t: WorkThread): number =>
    threadHoldsLease(row, t.id) ? 0 : t.running ? 1 : 2;
  return [...threads].sort((a, b) => {
    const d = rank(a) - rank(b);
    return d !== 0 ? d : at(b) - at(a);
  });
}

/**
 * Why a thread row carries no armed switch.
 *
 * Said ONCE under the thread list rather than as an absence on every row.
 * Section 1 of the design gives a thread an `armed` setting and section 7 lands
 * it in step 1; this view is step 3. Until then the only thing that decides
 * whether work advances unasked is the scope's execution mode, and pretending
 * otherwise on a per-thread control would be a switch with nothing behind it.
 */
export const THREAD_ARMED_NOTE =
  "No thread carries an armed setting yet. What runs here unasked is still set "
  + "per scope, in the app's settings.";

// --- the state of the change -------------------------------------------------

export type ChangeNote = {
  kind: StatusKind;
  label: string;
  /** What the change is, in the terms a review needs: which commit, into what. */
  detail: string;
};

/**
 * The state of this branch's change, or null when it has none.
 *
 * The status vocabulary is `changeReview.ts`'s, unchanged: it is the same six
 * states the review page and the changes list already print, and a second
 * spelling of them here would be a third answer to one question.
 */
export function changeNote(change: Change | null): ChangeNote | null {
  if (!change) return null;
  const sha = shortSha(change.head_sha);
  return {
    kind: changeStatusKind(change.status),
    label: changeStatusLabel(change.status),
    detail: sha ? `${sha} into ${change.base_ref}` : `Into ${change.base_ref}`,
  };
}

/** What the open row says where a change would be. A branch with no change is
 *  the ordinary early state of one, not a failure, so this is a sentence and
 *  never an error.
 *
 *  `available` is false when this instance keeps no change records at all
 *  (`/changes` answers 503, `changeReview.changesUnavailable`). That is a
 *  different fact from "nothing has opened yet" and must not read as the same
 *  sentence: one is a branch early in its life, the other is a store that was
 *  never configured. */
export function noChangeNote(row: WorkRow, available: boolean = true): string {
  if (!available) {
    return "This instance keeps no change records, so there is nothing to show "
      + "here. The branch and its commits are still real.";
  }
  const state = branchStatus(row.record, row.holder);
  if (state.label === "Merged" || state.label === "Discarded") {
    return "This branch is over and has no open change.";
  }
  return "No change has opened on this branch yet. One opens when it first has "
    + "commits its base does not.";
}

// --- the reads this view makes -----------------------------------------------

/**
 * The cache key for one app's changes.
 *
 * A key is never spelled at a call site (`scripts/check_frontend_state.py`
 * fails an array literal in a `queryKey`), and `lib/queryKeys.ts` has no
 * `changes` entry because nothing had read them through the query layer before.
 * It is a function returning a tuple, which is the same shape every factory in
 * that module has; move it there when the platform's next reader of `/changes`
 * appears, so the two cannot drift into two cache entries for one fact.
 *
 * `addr` is the query string the read was addressed with, so an app-scoped read
 * and a project-scoped one cannot be served each other's answer.
 */
export const workQueryKeys = {
  changes: (addr: string) => ["changes", "list", addr] as const,
};

/** How the changes read is addressed for one app: by its linked project when it
 *  has one, by app id otherwise. Empty when neither, which means there is
 *  nothing to ask and the answer is `[]` without a request. */
export function changesAddr(
  { projectSlug, appId }: { projectSlug?: string | null; appId?: string | null },
): string {
  if (projectSlug) return `project=${encodeURIComponent(projectSlug)}`;
  if (appId) return `app_id=${encodeURIComponent(appId)}`;
  return "";
}
