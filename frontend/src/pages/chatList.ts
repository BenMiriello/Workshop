// The pure rules behind the chat list: its ORDER, what narrows it, and the tags
// a row carries. Extracted because there is no DOM harness (frontend/CLAUDE.md):
// the target of a test is the pure rule behind a behaviour, so the rule has to
// live somewhere it can be executed. `chatPulse` beside this file is the same
// pattern.
//
// CHATS IS A FLAT LIST OF THREADS, NEWEST FIRST.
// `threads-branches-and-pages-2026-08-29.md` section 6: "Chats is a flat list of
// threads, newest first. It is the quick-jump surface. No branch headings, no
// grouping. (Reverted once already; it stays reverted.) A branch is a small tag
// on the row, next to scope and role."
//
// So there is ONE order here and no control that changes it. The page used to
// carry a three-value sort whose only real job was switching date headings on
// and off ("Recent" grouped the rows under Today / Yesterday / Earlier this
// week); the headings are the grouping section 6 names, and the control existed
// to produce them. Both are gone together, which is the rule that document
// opens with: a change deletes the surface it replaces.
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
