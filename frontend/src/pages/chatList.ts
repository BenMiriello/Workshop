// The pure rules behind the chat list: its ORDER, what narrows it, and the tags
// a row carries. Extracted because there is no DOM harness (frontend/CLAUDE.md):
// the target of a test is the pure rule behind a behaviour, so the rule has to
// live somewhere it can be executed. `chatPulse` beside this file is the same
// pattern.
//
// ONE ORDER, NEWEST FIRST, UNDER DATE HEADINGS.
//
// `threads-branches-and-pages-2026-08-29.md` section 6 says "Chats is a flat
// list of threads, newest first... No branch headings, no grouping. (Reverted
// once already; it stays reverted.)" On 2026-09-01 that sentence was read as
// banning ALL grouping, and commit 64539d5d deleted the date buckets with the
// sort control that switched them on.
//
// THAT WAS A MISREADING, and the operator corrected it the same day: "its the
// page where you have different groupings by today yesterday etc... just put
// that back on all chats pages." Read the paragraph it sits in and the subject
// is BRANCHES -- the surrounding clauses are "no branch headings" and "a branch
// is a small tag on the row", and the thing "reverted once already" is the
// branch-organised list that section's addendum spends a page arguing against.
// Date is not an organising axis competing with recency; it IS recency, named.
// A heading over a run of rows the reader is already scanning in that order
// adds no second structure and takes nothing away.
//
// The SORT CONTROL does not come back. It was three values whose only real job
// was switching these headings off, and nobody wants a chat list oldest-first.
// The headings are unconditional, so there is nothing to control.
import { branchName } from "../components/chat/chatBranch";
import { scopeName } from "../components/chat/chatScope";
import type { ThreadArmed } from "../lib/chat";

/** The list's one order: most recently active first.
 *
 *  `updated_at` and not `created_at`, because that is the field the row's own
 *  age reads (`relTime` in ChatRow), so a row's position and the time printed
 *  on it always agree. */
export function newestFirst<T extends { updated_at?: string | null }>(threads: readonly T[]): T[] {
  return [...threads].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}

/** The four values of `armed`, section 1's closed set. */
export const ARMED_VALUES: readonly ThreadArmed[] = ["off", "on-message", "schedule", "on-ready-ticket"];

/** A thread's arming, defaulting to `off`.
 *
 *  Missing reads as `off` and so does anything outside the closed set: the
 *  field is not on the wire yet (see `ThreadSummary` in lib/chat), and "this
 *  thread does not move without a person" is both the default section 1 gives
 *  and the only honest answer when nobody has said otherwise. */
export function armedOf(t: { armed?: string | null }): ThreadArmed {
  const v = (t.armed || "").trim();
  return (ARMED_VALUES as readonly string[]).includes(v) ? (v as ThreadArmed) : "off";
}

/** The date headings, oldest bucket last. Exported so a caller cannot invent
 *  its own order for them (the map they come out of has insertion order, which
 *  is the list's order, which is not the same thing when a bucket is empty). */
export const BUCKET_ORDER = ["today", "yesterday", "week", "month", "older"] as const;

/**
 * Which date heading a chat sits under.
 *
 * Off `updated_at`, the same field `newestFirst` orders by and the same one the
 * row's own age prints, so a row's heading, its position and its "3d" can never
 * disagree. Bucketed by CALENDAR DAY, not by elapsed hours: at 00:30 a message
 * from 23:00 is "Yesterday", which is what a reader means by the word, and
 * "25 hours ago" is not.
 *
 * An unparseable or missing timestamp falls to the last bucket rather than
 * being dropped: a chat with a bad date is still a chat, and a list that
 * silently omits rows is the failure this whole surface keeps having.
 */
export function bucketFor(iso: string | null | undefined, now: Date = new Date()):
    { key: string; label: string } {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return { key: "older", label: "Earlier" };
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = startOfDay(now);
  const day = 86_400_000;
  const ts = startOfDay(t);
  if (ts >= today) return { key: "today", label: "Today" };
  if (ts >= today - day) return { key: "yesterday", label: "Yesterday" };
  if (ts >= today - 7 * day) return { key: "week", label: "Earlier this week" };
  if (ts >= today - 30 * day) return { key: "month", label: "Earlier this month" };
  return { key: "older", label: "Earlier" };
}

export type ChatGroup<T> = { key: string; label: string; items: T[] };

/**
 * The rows, in order, cut into date groups.
 *
 * Takes the list ALREADY ordered and never reorders it, so grouping cannot
 * change which chat is at the top. An empty bucket does not appear at all: a
 * heading over nothing tells the reader there is nothing, which they can see.
 */
export function groupByDate<T extends { updated_at?: string | null }>(
  rows: readonly T[],
  now: Date = new Date(),
): ChatGroup<T>[] {
  return groupByField(rows, (t) => t.updated_at, now);
}

/** The same cut, over whatever date the surface is actually ordered by.
 *
 *  The archive is the caller: it comes back most-recently-ARCHIVED first, which
 *  answers "where did the one I just archived go", and a heading taken from
 *  `updated_at` there would name a different day from the row underneath it. A
 *  heading must always read the field its list is sorted by. */
export function groupByField<T>(
  rows: readonly T[],
  dateOf: (t: T) => string | null | undefined,
  now: Date = new Date(),
): ChatGroup<T>[] {
  const byKey = new Map<string, ChatGroup<T>>();
  for (const t of rows) {
    const b = bucketFor(dateOf(t), now);
    if (!byKey.has(b.key)) byKey.set(b.key, { key: b.key, label: b.label, items: [] });
    byKey.get(b.key)!.items.push(t);
  }
  return BUCKET_ORDER.filter((k) => byKey.has(k)).map((k) => byKey.get(k)!);
}

/** "A thread that is armed is what used to be called an agent" (section 1). */
export function isArmed(t: { armed?: string | null }): boolean {
  return armedOf(t) !== "off";
}

/** What the list is narrowed to. A view preference, so it commits on change. */
export type ArmedFilter = "all" | "armed";

/** The rows the page renders: narrowed, then ordered. One function, because the
 *  two together are what "the list" means and doing them in the other order
 *  would sort rows that are about to be dropped. */
export function visibleThreads<T extends { armed?: string | null; updated_at?: string | null }>(
  threads: readonly T[] | null | undefined,
  filter: ArmedFilter,
): T[] {
  const list = threads ?? [];
  return newestFirst(filter === "armed" ? list.filter(isArmed) : list);
}

// ---- The row's tags --------------------------------------------------------

export type ChatTagKind = "scope" | "role" | "branch";
export type ChatTag = { kind: ChatTagKind; label: string };

/** What a thread carries as small tags, in section 6's order: scope, role,
 *  branch.
 *
 *  Each one is OMITTED when the thread does not have it, never rendered as a
 *  placeholder. A null role is a perfectly good value (section 1: "an armed
 *  thread with no role continues with its own context"), so a row without one
 *  says nothing rather than "none", which would read as a state somebody chose.
 *  A thread not on a branch works in the shared tree and has no branch to name.
 *
 *  The branch's readable name comes from `branchName`, the same rule the chat
 *  toolbar's pill and its move list use, so a 32-character hex slug is shortened
 *  here exactly as it is there. */
/**
 * Whether the rows in hand actually differ in scope.
 *
 * THE SCOPE TAG IS ONLY INFORMATION WHEN IT VARIES. On an app's own Chats tab
 * every row is that app, so a scope tag on each one is the page's own title
 * repeated once per row and nothing else. Reported 2026-09-01 on the app that
 * is this repo: every row read "Development" on the Development page.
 *
 * Same rule and same reason as `spansScopes` for tickets (lib/tasks.ts). The
 * old rule there asked the FILTER, which is why an unfiltered list still
 * printed one scope name fifty times; this asks the ROWS.
 */
export function spansScopes(rows: readonly { context_key?: string | null }[]): boolean {
  const seen = new Set<string>();
  for (const r of rows) {
    seen.add(scopeName(r.context_key || "") || "");
    if (seen.size > 1) return true;
  }
  return false;
}

export function chatTags(
  t: {
    context_key?: string | null;
    role?: string | null;
    branch_slug?: string | null;
  },
  /** False on a list that is all one scope, where the tag says nothing. */
  showScope = true,
): ChatTag[] {
  const tags: ChatTag[] = [];
  const scope = showScope ? scopeName(t.context_key || "") : "";
  if (scope) tags.push({ kind: "scope", label: scope });
  const role = (t.role || "").trim();
  if (role) tags.push({ kind: "role", label: role });
  const branch = branchName({ branch_slug: t.branch_slug });
  if (branch) tags.push({ kind: "branch", label: branch });
  return tags;
}


/**
 * The facts on a chat row's meta line, in order, as PLAIN TEXT.
 *
 * The design law's own words about this line, in components.css beside
 * `.ws-listrow__meta`: "Plain muted text, not tags. Scope and age are things
 * you read once you are already looking at a row; a chip is for something you
 * scan FOR. Spending the chip treatment on both is what stops the one mark
 * that matters from standing out at all."
 *
 * The chat row used to do both at once: a model and a turn count on line one,
 * and scope, role and branch as coloured chips on line two. Same rule and same
 * shape as `ticketRowFacts` in lib/tasks, so the two lists read alike.
 *
 * A fact is omitted rather than defaulted. No "0 turns", no "none" for a role
 * a thread does not have.
 */
export function chatRowFacts(
  t: {
    context_key?: string | null;
    model?: string | null;
    message_count?: number | null;
    role?: string | null;
    branch_slug?: string | null;
  },
  opts: { showScope?: boolean; shortModel?: (m: string) => string } = {},
): string[] {
  const out: string[] = [];
  const model = (t.model || "").trim();
  if (model) out.push(opts.shortModel ? opts.shortModel(model) : model);
  if (typeof t.message_count === "number" && t.message_count > 0) {
    out.push(`${t.message_count} ${t.message_count === 1 ? "turn" : "turns"}`);
  }
  if (opts.showScope) {
    const scope = scopeName(t.context_key || "");
    if (scope) out.push(scope);
  }
  const role = (t.role || "").trim();
  if (role) out.push(role);
  const branch = branchName({ branch_slug: t.branch_slug });
  if (branch) out.push(branch);
  return out;
}
