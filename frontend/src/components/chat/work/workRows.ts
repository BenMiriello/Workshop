// WHICH work the panel shows, and in what order. The rule, not the render.
//
// Extracted so it can be executed: this repo has no DOM harness and is not
// getting one, so the target of a test is always the pure rule behind a
// behaviour (`turnBatching`, `transcriptBlocks`). Everything the Work panel
// decides that is not layout lives here: which chip is on when it opens, which
// rows a chip admits, how those rows group and sort, and where the list stops.
//
// THE CHIPS ARE NOT FILTERS OVER ONE LIST. `Needs you` is inbox items, the
// other three are tickets, and a chip that is empty still renders: the four
// counts are the panel's summary of the chat's work, so hiding a zero would
// move the other three sideways every time an agent raised something.
//
// The counts come from the SERVER (`data.counts`), never from the rows on
// screen. Rows are capped here and the server caps `scope` at eight before that,
// and both caps would otherwise be reported as "there is nothing else". Same
// reasoning the deleted `inboxOverflow` carried, kept because it was right.

import { jobIsLive } from "../../../lib/jobs";
import { taskStateTypeOf, type TaskStateType } from "../../../lib/tasks";
import type { InboxItem, WorkCounts, WorkPayload, WorkTicket, InboxKind } from "./workTypes";

export type WorkChip = "needs_you" | "running" | "this_chat" | "all";

export const WORK_CHIPS: { id: WorkChip; label: string }[] = [
  { id: "needs_you", label: "Needs you" },
  { id: "running", label: "Running" },
  { id: "this_chat", label: "This chat" },
  { id: "all", label: "All" },
];

// The panel is a popover, not a list page. Past this it links to the board
// rather than paginating: a second page inside a surface that closes on an
// outside click is a place work goes to be lost.
export const MAX_ROWS = 8;

// A group heading is a ticket's coarse state, plus one that is not a ticket
// state at all: `live` is a fact about the RUN, and it wins, because "an agent
// is working on this right now" is the answer the panel is opened for.
//
// `canceled` is here and is not in the design's list of five. A cancelled
// ticket filed by this chat is still in `filed`, and a row that matches no
// group is a row that silently disappears.
export type WorkGroupKey = "needs_you" | "live" | TaskStateType;

const GROUP_ORDER: WorkGroupKey[] = [
  "needs_you", "live", "started", "unstarted", "backlog", "completed", "canceled",
];

const GROUP_LABEL: Record<WorkGroupKey, string> = {
  needs_you: "Needs you",
  live: "Running",
  started: "Under way",
  unstarted: "To do",
  backlog: "Backlog",
  completed: "Done",
  canceled: "Cancelled",
};

export type WorkRow =
  | { kind: "item"; id: string; item: InboxItem }
  | { kind: "ticket"; id: string; ticket: WorkTicket };

export type WorkGroup = { key: WorkGroupKey; label: string; rows: WorkRow[] };

export type WorkList = {
  /** Only groups that have rows. An empty heading is a heading that lies. */
  groups: WorkGroup[];
  /** Whether to print the group headings at all.
   *
   *  A heading SEPARATES. With one group there is nothing to separate it from,
   *  and the heading then repeats the chip that is already selected directly
   *  above it: "Needs you" over rows chosen by the `Needs you` chip. Two words
   *  saying the same thing, twelve pixels apart, on the panel's most-used view.
   *  Each row already carries its own state word in its meta line, so nothing
   *  is lost by dropping the one heading and everything is kept when there are
   *  several. */
  headed: boolean;
  /** Rows actually rendered, after the cap. */
  shown: number;
  /** How big the chip's set is, as far as anything knows. */
  total: number;
  /** What the cap left out. Never negative. */
  overflow: number;
};

// WHICH CHIP OPENS. Needs-you when something is waiting, because that is the
// only one of the four that is an alarm; then this chat, because the panel was
// opened from a conversation and its own work is the nearest question; then
// All, which is never wrong and is what a quiet chat should show.
export function defaultChip(counts: WorkCounts): WorkChip {
  if (counts.needs_you > 0) return "needs_you";
  if (counts.this_chat > 0) return "this_chat";
  return "all";
}

export function chipCount(counts: WorkCounts, chip: WorkChip): number {
  if (chip === "needs_you") return counts.needs_you;
  if (chip === "running") return counts.running;
  if (chip === "this_chat") return counts.this_chat;
  return counts.all;
}

// `filed` and `scope` are disjoint on the server today (a ticket this chat filed
// into its own scope is excluded from `scope`), but the panel must not depend on
// that: one duplicate row would be one ticket claiming to be two pieces of work.
function dedupe(rows: WorkTicket[]): WorkTicket[] {
  const seen = new Set<string>();
  const out: WorkTicket[] = [];
  for (const t of rows) {
    if (seen.has(t.id)) continue;
    seen.add(t.id);
    out.push(t);
  }
  return out;
}

/** The tickets a chip admits. Empty for `Needs you`, which lists items. */
export function ticketsForChip(data: WorkPayload, chip: WorkChip): WorkTicket[] {
  if (chip === "needs_you") return [];
  if (chip === "this_chat") return dedupe(data.filed);
  const everything = dedupe([...data.filed, ...data.scope]);
  if (chip === "running") return everything.filter((t) => jobIsLive(t.run?.status));
  return everything;
}

function groupKeyFor(t: WorkTicket): WorkGroupKey {
  return jobIsLive(t.run?.status) ? "live" : taskStateTypeOf(t);
}

const stamp = (t: WorkTicket): number =>
  Date.parse(t.updated || t.created || "") || 0;

// Newest touched first, and the id breaks the tie. Two tickets written in the
// same second are common (a turn files three at once) and an unstable sort would
// reshuffle them on every poll.
function byNewest(a: WorkTicket, b: WorkTicket): number {
  return stamp(b) - stamp(a) || a.id.localeCompare(b.id);
}

/** The panel's rows for one chip: grouped, sorted, and capped. */
export function workList(data: WorkPayload, chip: WorkChip): WorkList {
  const counted = chipCount(data.counts, chip);

  if (chip === "needs_you") {
    const rows: WorkRow[] = data.inbox
      .slice(0, MAX_ROWS)
      .map((item) => ({ kind: "item", id: item.id, item }));
    const total = Math.max(counted, data.inbox.length);
    return {
      groups: rows.length
        ? [{ key: "needs_you", label: GROUP_LABEL.needs_you, rows }]
        : [],
      headed: false,
      shown: rows.length,
      total,
      overflow: Math.max(0, total - rows.length),
    };
  }

  const tickets = ticketsForChip(data, chip);
  const buckets = new Map<WorkGroupKey, WorkTicket[]>();
  for (const t of tickets) {
    const key = groupKeyFor(t);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(t);
    else buckets.set(key, [t]);
  }

  const groups: WorkGroup[] = [];
  let budget = MAX_ROWS;
  for (const key of GROUP_ORDER) {
    if (budget <= 0) break;
    const bucket = buckets.get(key);
    if (!bucket?.length) continue;
    const take = [...bucket].sort(byNewest).slice(0, budget);
    budget -= take.length;
    groups.push({
      key,
      label: GROUP_LABEL[key],
      rows: take.map((ticket) => ({ kind: "ticket", id: ticket.id, ticket })),
    });
  }

  const shown = MAX_ROWS - budget;
  // The larger of what the server counted and what it actually sent. `all`
  // counts ACTIVE tickets in the chat's scope, so a chat that filed a done
  // ticket, or one into another scope, carries rows the count does not know
  // about; taking the count alone would report a negative overflow as zero and
  // claim a capped list was complete.
  const total = Math.max(counted, tickets.length);
  return {
    groups,
    headed: groups.length > 1,
    shown,
    total,
    overflow: Math.max(0, total - shown),
  };
}

// The first two are kept VERBATIM from the surfaces this replaces. They were
// right, and rewriting an empty state that already says what to do next is how
// a rebuild loses the one sentence someone thought about.
//
// The other two exist because an empty chip has to describe the set that is
// empty. "Nothing under way in this scope" under `This chat` says the wrong
// thing twice: the set is this conversation's tickets, not the scope's, and a
// filed ticket sitting in `to do` is not "under way". An empty state that
// misdescribes what is empty sends the reader looking for a filter they did not
// set.
export const NOTHING_WAITING =
  "Nothing waiting on you. Agents raise questions and reviews here.";
export const NOTHING_UNDER_WAY =
  "Nothing under way in this scope. File work as a ticket and an agent can pick it up.";
export const NOTHING_RUNNING =
  "Nothing is running. A ticket shows here while an agent is working on it.";
export const NOTHING_FILED_HERE =
  "No tickets filed from this chat yet.";

const EMPTY_TEXT: Record<WorkChip, string> = {
  needs_you: NOTHING_WAITING,
  running: NOTHING_RUNNING,
  this_chat: NOTHING_FILED_HERE,
  all: NOTHING_UNDER_WAY,
};

export function emptyText(chip: WorkChip): string {
  return EMPTY_TEXT[chip];
}


/** The verb that turns a raiser's name into a sentence, by what they raised. */
const KIND_VERB: Record<InboxKind, string> = {
  question: "asks",
  review: "proposes",
  notify: "says",
};

/** The SECOND line of a needs-you card: who raised it, and what they said.
 *
 *  Absent rather than empty when there is nothing to add, which is the common
 *  case: a finished ticket has no question beyond its status (the server sends
 *  an empty ask for exactly that reason), and a ticket the queue picked up has
 *  no agent identity at all. The card's first line is the ticket TITLE, so this
 *  line only ever exists to add something the title does not carry.
 *
 *  The VERB only appears when an ask follows it. "Autonomous run asks" with
 *  nothing after it is a sentence that stops before its object; the name alone
 *  is the honest form of "who did this". */
export function needsYouMeta(
  agent: string | null | undefined,
  kind: InboxKind,
  ask: string,
): string {
  const who = (agent || "").trim();
  const said = (ask || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
  if (who && said) return `${who} ${KIND_VERB[kind] ?? "says"} \u00b7 ${said}`;
  return who || said;
}
