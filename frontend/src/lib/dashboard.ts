// WHAT THE DASHBOARD SHOWS, and the four reads behind it.
//
// The page is four fixed-height cards: what needs you, what you were doing,
// what is running, and the apps. Each card is one independent server read, so a
// slow or failed endpoint degrades ONE card into its own error state instead of
// blanking the page.
//
// EVERY DERIVATION IS A PURE FUNCTION HERE, not a line inside a component. This
// repo has no DOM harness and is not getting one, so the target of a test is
// always the rule behind a behaviour. What is genuinely React (the polling, the
// loading flags) stays in the components; what is a decision (which rows fit,
// what a row says, how a spend reads) is below and is tested.
//
// NO NEW DATA LAYER. There is no query library in this workspace's non-chat
// surfaces: what exists is `usePoll`, `shortHttpError` and per-domain modules
// like `lib/usage` and `lib/jobs` that own a shape and a fetch. This is one
// more of those. Chat has since moved onto TanStack Query; these four reads are
// plain GETs with no streaming, no pagination and no optimistic write, so they
// are the easiest possible thing to move later and there is no reason to make
// them the pilot.
import { shortHttpError } from "@workshop/ui";
import type { components } from "../api/schema.gen";
import type { Job } from "./jobs";

export type ThreadSummary = components["schemas"]["ThreadSummary"];
// Two models are named InboxItem on the wire (this route's and the chat work
// payload's); the generator qualifies them, so name the one this reads.
export type InboxItem = components["schemas"]["routes__inbox__InboxItem"];
export type InboxList = components["schemas"]["InboxList"];
export type UsageStatus = components["schemas"]["UsageStatus"];

// ---- fetching --------------------------------------------------------------

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as T;
}

/** What wants the operator, across every scope. One route, and the same column
 *  a chat's work panel shows -- see backend services/chat_work.needs_you. */
export const fetchNeedsYou = (limit = 12) =>
  get<InboxList>(`/api/inbox?limit=${limit}`);

/** The conversations, newest-spoken-in first. Rows carry `running` and the
 *  context pair, so the card needs no per-chat follow-up call. */
export const fetchThreads = () => get<ThreadSummary[]>("/api/chat/threads");

/** Runs in flight. `page.total` is null on this endpoint by design (ADR 0023
 *  clause 1) and a null total is not zero, so the count comes from the rows. */
export const fetchRunning = (limit = 12) =>
  get<{ items: Job[] }>(`/api/jobs?status=running&limit=${limit}`).then((p) => p.items ?? []);

/** The rolling budget window. Tokens, not money: this system has no price
 *  table, and a dollar figure would have to be invented. */
export const fetchUsage = () => get<UsageStatus>("/api/usage?window=5h");

// ---- rows ------------------------------------------------------------------

export type NeedsRow = {
  id: string;
  /** Who is asking. An agent name when one is recorded, else the scope. */
  who: string;
  scope: string;
  ask: string;
  when: string | null;
  /** Where tapping the row goes. A ticket-derived row opens its ticket; an
   *  ambient notice has no record of its own, so it opens the waiting list. */
  to: string;
};

export type ContinueRow = {
  id: string;
  title: string;
  tail: string;
  running: boolean;
  when: string | null;
  /** null when no turn has completed: a list row does not estimate. */
  used: number | null;
  win: number | null;
  to: string;
};

export type RunningRow = {
  id: string;
  name: string;
  doing: string;
  when: string | null;
  to: string;
};

const firstLine = (s: string | null | undefined) =>
  String(s ?? "").split("\n").find((l) => l.trim()) ?? "";

export function needsRows(list: InboxList | null, cap: number): NeedsRow[] {
  return (list?.items ?? []).slice(0, cap).map((i) => ({
    id: i.id,
    who: i.agent || i.scope || "workshop",
    scope: i.scope || "",
    ask: firstLine(i.title) || firstLine(i.body),
    when: i.created_at ?? null,
    // A waiting TICKET has a record to open. An ambient notice does not: it is
    // answered where it was raised, so the row goes to the list rather than to
    // a page that would have to be invented for it.
    to: i.ticket_id ? `/tickets/${encodeURIComponent(i.scope)}/${i.ticket_id}` : "/tickets",
  }));
}

export function continueRows(threads: ThreadSummary[] | null, cap: number): ContinueRow[] {
  return (threads ?? []).slice(0, cap).map((t) => ({
    id: t.id,
    title: t.title || "Untitled chat",
    tail: firstLine(t.last_message),
    running: !!t.running,
    when: t.last_user_at || t.updated_at || null,
    used: t.context_tokens ?? null,
    win: t.context_window ?? null,
    to: `/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`,
  }));
}

export function runningRows(jobs: Job[] | null, cap: number): RunningRow[] {
  return (jobs ?? []).slice(0, cap).map((j) => ({
    id: j.id,
    // `agent_name` is the display name the API resolved; `title` is the run's
    // own sentence. The name is what the row leads with, because "what is
    // running" is answered by who, not by which task id.
    name: j.agent_name || j.trigger_label || "run",
    doing: firstLine(j.summary || j.task_text || j.title),
    when: j.started_at ?? null,
    to: `/runs/${j.id}`,
  }));
}

// ---- the spend line --------------------------------------------------------

/** The footer under "Running now".
 *
 *  TOKENS, NOT MONEY, and it says which window. The mockup reads "$4.12 today"
 *  and neither half of that exists: the ledger counts tokens
 *  (services/usage_ledger), no price table exists anywhere in the tree, and the
 *  only window with a defined ceiling is the rolling 5h one -- "today" is a
 *  different measurement that nothing computes. Rendering a dollar figure would
 *  mean inventing a number, which is the one thing a spend line must never do.
 */
export function spendLine(usage: UsageStatus | null): string {
  if (!usage) return "";
  const used = formatTokens(usage.app_used ?? 0);
  const total = usage.app_total;
  return total ? `${used} of ${formatTokens(total)} · 5h` : `${used} · 5h`;
}

/** Same rounding as `lib/usage`'s own formatter, which the Usage page uses. */
export function formatTokens(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

// ---- how many rows fit -----------------------------------------------------

/** Rows a card can show at a given height, and how many that leaves out.
 *
 *  THE CARD IS A FIXED HEIGHT AND CLIPS. That is the layout rule the mockup is
 *  built on, and it is what makes the grid read as a grid rather than as four
 *  boxes of different lengths. So a card shows a WHOLE number of rows and says
 *  what it left out on its own "→" link -- it never half-draws a row at the
 *  clip edge and never grows to fit.
 */
export function fitRows<T>(rows: T[], cap: number): { shown: T[]; hidden: number } {
  const shown = rows.slice(0, Math.max(0, cap));
  return { shown, hidden: Math.max(0, rows.length - shown.length) };
}

/** The label a "→" link carries once rows are being left out. Plain "→" while
 *  everything fits, because "+0 more" is noise. */
export function moreLabel(hidden: number, verb: string): string {
  return hidden > 0 ? `${hidden} more ${verb} →` : `${verb} →`;
}
