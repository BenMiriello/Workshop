// What the chat's work surface is made of, on the wire.
//
// ONE PAYLOAD, one shape, read from `GET /api/chat/threads/:id/work`. The badge,
// the Work panel and every ticket card in the transcript read this and nothing
// else, which is the only way three surfaces can be prevented from disagreeing
// about one ticket. Before this they were three pollers against two endpoints
// and each card would have added two more.
//
// `InboxItem` moved here from `InboxStrip`, which no longer exists. It is the
// server's row and the type says so.

import type { JobRefusal } from "../../../lib/jobs";
import type { Task } from "../../../lib/tasks";

export type InboxKind = "notify" | "question" | "review";

export type InboxItem = {
  id: string;
  kind: InboxKind;
  agent: string;
  scope: string;
  title: string;
  body: string;
  // Offered answers. Free text is always allowed alongside them: the resolve
  // endpoint takes an arbitrary string and an option is just one of those.
  options: string[];
  chat_id: string | null;
  // What this item is ABOUT. An inbox row that cannot reach the work it
  // describes makes the operator go find it by hand, which is most of the
  // reason the old attention surfaces went unread.
  ticket_id: string | null;
  run_id: string | null;
  // Deferred until this instant. The server filters snoozed rows out of the
  // listing AND out of the counts, so this never arrives set on a live row; it
  // is here because the row is the server's item and the type should say so.
  snoozed_until: string | null;
};

// The latest run of a ticket, summarised.
//
// EVERY FIELD HERE EXISTS. The design handoff drew a "latest tool call" line on
// the card and there is no such thing on a run: tool events belong to chat
// turns, a job carries no in-flight step data, and there is no run stream. What
// a run can actually say about itself is who ran it, when it started, how it
// ended, and what the receipt counted.
export type WorkRun = {
  id: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  agent_name: string | null;
  trigger: string | null;
  exit_code: number | null;
  // From the receipt in `evidence`, when it is one. Counts, not lists: the card
  // says "4 files, 2 commits" and the run page holds the names.
  files: number | null;
  commits: number | null;
  // WHY IT STOPPED, when it stopped before working anything. `halted` is the
  // sentence; `refusal` is set only when a GUARD is what stopped it, and is what
  // turns the card's state word from `cancelled` (which reads as a crash) into
  // `refused` (a guard doing its job).
  halted: string | null;
  refusal: JobRefusal | null;
};

// A ticket as this surface reads it: the ticket, plus its latest run or null.
// `run` is null both for a ticket that has never run and for one whose runs
// have been pruned, and the card renders the same way for both, because from
// the chat's point of view they are the same fact.
export type WorkTicket = Task & { run: WorkRun | null };

export type WorkCounts = {
  needs_you: number;
  running: number;
  this_chat: number;
  all: number;
};

export type WorkPayload = {
  inbox: InboxItem[];
  // Tickets filed BY this chat, any scope, newest first. The edge is the
  // ticket's `source.ref`, not the run's `chat_id`, which is declared and never
  // written.
  filed: WorkTicket[];
  // Active tickets in the chat's task scope that this chat did not file.
  scope: WorkTicket[];
  counts: WorkCounts;
  // The chat's task scope, or null for a chat that has none (general, apps).
  scope_key: string | null;
};

export const EMPTY_WORK: WorkPayload = {
  inbox: [],
  filed: [],
  scope: [],
  counts: { needs_you: 0, running: 0, this_chat: 0, all: 0 },
  scope_key: null,
};
