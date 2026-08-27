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
// THE FETCHES ARE HERE; THE CADENCE IS NOT. Each function below is one plain
// GET that owns a shape, and the cards call them as query functions. When these
// four reads were written they were `usePoll` call sites, which was already the
// old mechanism by then; they have since moved onto the query layer
// (`lib/queryClient.ts`) with the same URLs and the same intervals. Nothing in
// this module knows how often it is called.
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

/** Runs in flight.
 *
 *  `page.total` is null on this endpoint BY DESIGN (ADR 0023 clause 1: the job
 *  log is collapsed by id and stops at `limit`, so a total would mean a second
 *  full pass), and a null total must not be read as zero. So the count comes
 *  from the rows, and the limit is set high enough that the count is the real
 *  one rather than the page's. */
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
  /** WHAT IT WANTS FROM YOU, in a word. Not decoration: this card mixes two
   *  different requests -- a question that has stopped an agent, and finished
   *  work waiting to be accepted -- and the row is otherwise identical for
   *  both, so it read as "someone is asking" over things nobody was asking. */
  wants: string;
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

export function needsRows(list: InboxList | null): NeedsRow[] {
  return (list?.items ?? []).map((i) => ({
    id: i.id,
    who: i.agent || i.scope || "workshop",
    // The server's own vocabulary (inbox.KINDS), said in the operator's words.
    // A `notify` never reaches here -- it wants nothing, so it is not in this
    // column at all (inbox.WANTS_YOU_KINDS).
    wants: i.kind === "review" ? "for review" : "asks",
    // BLANK WHEN IT WOULD REPEAT `who`. A row with no agent recorded falls back
    // to naming its scope, and a chip beside it saying the same word twice is
    // noise wearing a label's clothing.
    scope: i.agent && i.scope ? i.scope : "",
    ask: firstLine(i.title) || firstLine(i.body),
    when: i.created_at ?? null,
    // A waiting TICKET has a record to open. An ambient notice does not: it is
    // answered where it was raised, so the row goes to the list rather than to
    // a page that would have to be invented for it.
    to: i.ticket_id ? `/tickets/${encodeURIComponent(i.scope)}/${i.ticket_id}` : "/tickets",
  }));
}

export function continueRows(threads: ThreadSummary[] | null): ContinueRow[] {
  return (threads ?? []).map((t) => ({
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

/** What is moving right now: runs in flight AND chats with a turn in flight.
 *
 *  BOTH, because the question the card answers is "what is happening without
 *  me" and a streaming chat is one of those. Showing only jobs said "Nothing
 *  running" on an instance with two live turns on it, which is the kind of
 *  confident wrong answer that teaches an operator to stop reading a card.
 *
 *  Runs first: a chat is running because you left it open, a job is running on
 *  its own, and the second is the one you are less likely to know about.
 */
export function runningRows(
  jobs: Job[] | null, threads: ThreadSummary[] | null,
): RunningRow[] {
  const runs: RunningRow[] = (jobs ?? []).map((j) => ({
    id: j.id,
    // `agent_name` is the display name the API resolved; `title` is the run's
    // own sentence. The name is what the row leads with, because "what is
    // running" is answered by who, not by which task id.
    name: j.agent_name || j.trigger_label || "run",
    doing: firstLine(j.summary || j.task_text || j.title),
    when: j.started_at ?? null,
    to: `/runs/${j.id}`,
  }));
  const chats: RunningRow[] = (threads ?? []).filter((t) => t.running).map((t) => ({
    id: `chat:${t.id}`,
    name: t.title || "Untitled chat",
    doing: "answering",
    when: t.last_user_at || t.updated_at || null,
    to: `/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`,
  }));
  return [...runs, ...chats];
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

// ---- overflow -------------------------------------------------------------

/** The label a "→" link carries once rows are being left out.
 *
 *  ONLY THE SERVER'S OWN CAP PUTS A NUMBER HERE. A card scrolls, so it never
 *  leaves a row out for want of room; the only thing it can be missing is what
 *  it did not ask for -- more waiting items than its page limit. Plain "→"
 *  while everything fetched is on screen, because "+0 more" is noise. */
export function moreLabel(hidden: number, verb: string): string {
  return hidden > 0 ? `${hidden} more ${verb} →` : `${verb} →`;
}
