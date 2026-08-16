// WHAT A TICKET CARD SHOWS, given a ticket and its latest run. The rule, not
// the render.
//
// Extracted so it can be executed. This repo has no DOM harness and is not
// getting one, so the target of a test is always the pure rule behind a
// behaviour (`transcriptBlocks`, `turnBatching`, `workRows`). A card with ten
// states and no way to run them is a card whose tenth state is discovered by
// the operator.
//
// THE CARD'S SUBJECT IS THE TICKET. ADR 0009 forbids two status indicators for
// one entity in one view and requires a view holding two entities to separate
// them into labelled sections; a card carrying a ticket status and a run status
// is the case it names. The resolution is decided in
// docs/platform-redesign/tickets-in-chat-2026-08-15.md §4 and implemented here:
// the header status is the TICKET's and is canonical for it, the run's status
// belongs to the Run group, and the two are never in the same row. A run that
// failed under a ticket that is still `todo` therefore reads as the two true
// facts it is, rather than as one invented status that is neither.
//
// A GROUP WITH NO DATA IS NOT HERE AT ALL. Never a placeholder row, never a
// label with an empty value beside it. `labels` is empty on 97% of live tickets
// and `blocked_by` on almost all of them, so a card that reserved a line for
// each would be mostly furniture.
//
// THE TEN STATES (plan §4), and where each one is decided below:
//
//   1. Proposal, not yet filed .. never reaches here. `UiCard` is still in its
//      edit phase and this module is only consulted once a ticket exists.
//   2. Filed, never run ........ `ready`, no Run group, actions open + runNow.
//   3. Running ................. `ready`, Run group with a LIVE clock.
//   4. Blocked, awaiting ....... `ready`, plus the `waiting` note.
//   5. In review ............... `ready`; the header pill carries it.
//   6. Done .................... `ready`; the Run group keeps its receipt.
//   7. Run failed, ticket not .. `ready`; two statuses, two sections.
//   8. Merged .................. `ready`, plus the filed note that says so.
//   9. Ticket deleted .......... `deleted`. The card is still on screen, so the
//      component says it in a LINE and never a region.
//  10. Scope cannot run work ... `ready` with no runNow, and the pickup warning
//      stands as the note.
//
// And the four the card has to survive because it reads from a poll: nothing
// read yet (`cold`), the ticket gone (`deleted`), the read failed
// (`unreadable`, or `problem` when a copy is still on screen), and a run that
// left no receipt (`run.receipt === null`).

import type { StatusKind } from "@workshop/ui";
import { jobIsLive, jobStatusKind } from "../../../lib/jobs";
import { pickupNote, type Pickup } from "../../../lib/scopePickup";
import {
  description, taskStatusKind, taskStatusLabel, ticketKey, title as taskTitle,
  type TaskLink,
} from "../../../lib/tasks";
import { scopeName } from "../chatScope";
import { elapsedSeconds } from "../turnStatus";
import type { InboxItem, WorkRun, WorkTicket } from "./workTypes";

export type TicketCardPhase = "cold" | "ready" | "deleted" | "unreadable";

export type TicketCardGroupKey = "task" | "ticket" | "run";

export type TicketCardActionId = "open" | "viewRun" | "runNow";

// The only three actions the card carries, because they are the only three that
// resolve to something that exists. There is NO Stop: no endpoint stops a run.
// `POST /jobs/{id}/tasks/{tid}/action` cancels a waiting task within a job,
// which is a different thing, and a button that cannot work is worse than an
// absent one. Recorded as a real gap in the plan's §9.
export const TICKET_CARD_ACTION_LABEL: Record<TicketCardActionId, string> = {
  open: "Open ticket",
  viewRun: "View run",
  runNow: "Run now",
};

export type TicketCardProp =
  | { key: "key"; label: string; scope: string; text: string }
  | { key: "blocked_by"; label: string; links: TaskLink[] }
  | { key: "labels"; label: string; labels: string[] }
  | { key: "effort"; label: string; text: string };

export type TicketCardClock = { live: boolean; text: string };

export type TicketCardRun = {
  id: string;
  /** The RUN's state word. Never merged with the ticket's. */
  label: string;
  kind: StatusKind;
  live: boolean;
  agent: string | null;
  trigger: string | null;
  /** Elapsed while it runs, the final duration once it has stopped, and null
   *  when there is no start time to count from. */
  clock: TicketCardClock | null;
  /** Null when the run left NO receipt. That is not the same fact as a receipt
   *  that counted zero, and only the second one is worth printing. */
  receipt: { files: number | null; commits: number | null } | null;
};

export type TicketCardNote =
  | { kind: "waiting"; itemId: string; agent: string; text: string }
  | { kind: "muted"; text: string };

export type TicketCardPlan = {
  phase: TicketCardPhase;
  title: string;
  /** The header pill. The TICKET's status, and there is no second one here. */
  status: { label: string; kind: StatusKind } | null;
  /** Labelled groups, in render order. Absent means it has no data at all. */
  groups: TicketCardGroupKey[];
  /** The Task group's content. */
  body: string;
  /** The Ticket group's rows, in order. */
  props: TicketCardProp[];
  /** The Run group's content: the LATEST run only. */
  run: TicketCardRun | null;
  actions: TicketCardActionId[];
  /** The ONE line under the card, or none. */
  note: TicketCardNote | null;
  /** A LINE inside the card: the card is still on screen, so a failure beside
   *  it is never a region (frontend/CLAUDE.md, "A region or a line"). */
  problem: string | null;
};

export type TicketCardInput = {
  /** The ticket this card filed. */
  id: string;
  /** Where the ticket lives, for the key line and the run endpoint. */
  scope: string;
  /** The tracked copy: the shared store's, or the one-shot fallback read's. */
  ticket: WorkTicket | null;
  /** The title the filing recorded, so a cold card still names its subject. */
  filedTitle: string;
  /** The ticket was looked for and the server said it is gone. */
  deleted: boolean;
  /** The last read failed. Never blanks a ticket that is already on screen. */
  error: string | null;
  /** Who picks work up in this scope. See lib/scopePickup. */
  pickup: Pickup | null;
  /** An open inbox item that names this ticket, if one does. */
  waiting: InboxItem | null;
  /** The one thing about the filing the ticket cannot re-derive: that it merged
   *  into existing work, or that the run could not start. Survives a reload in
   *  the card-state sidecar, which is the card's only memory. */
  filedNote: string | null;
  nowMs: number;
};

export const DELETED_TEXT = "This ticket no longer exists.";

/** A duration, at the one scale that fits it.
 *
 *  Byte-identical to `formatElapsed` under an hour, deliberately: the panel row
 *  and the card show the same run and must not spell its clock two ways. Hours
 *  are added because a finished run can be long and `93m 04s` is a number
 *  nobody reads. */
export function clockText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${(s % 60).toString().padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${(m % 60).toString().padStart(2, "0")}m`;
}

/** The run's state word. Only `awaiting_input` carries an underscore, and an
 *  underscore reads poorly in prose. */
export function runStatusLabel(status?: string): string {
  return (status || "unknown").replace(/_/g, " ");
}

/** What the receipt counted, or "" when there was no receipt to count. */
export function receiptText(receipt: TicketCardRun["receipt"]): string {
  if (!receipt) return "";
  const parts: string[] = [];
  if (receipt.files != null) {
    parts.push(`${receipt.files} file${receipt.files === 1 ? "" : "s"} changed`);
  }
  if (receipt.commits != null) {
    parts.push(`${receipt.commits} commit${receipt.commits === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function runFor(run: WorkRun, nowMs: number): TicketCardRun {
  const live = jobIsLive(run.status);
  const startedMs = run.started_at ? Date.parse(run.started_at) : NaN;
  const endedMs = run.ended_at ? Date.parse(run.ended_at) : NaN;
  let clock: TicketCardClock | null = null;
  if (Number.isFinite(startedMs)) {
    if (live) {
      clock = { live: true, text: clockText(elapsedSeconds(startedMs, nowMs)) };
    } else if (Number.isFinite(endedMs)) {
      clock = { live: false, text: clockText((endedMs - startedMs) / 1000) };
    }
  }
  return {
    id: run.id,
    label: runStatusLabel(run.status),
    kind: jobStatusKind(run.status),
    live,
    agent: run.agent_name || null,
    trigger: run.trigger || null,
    clock,
    // NULL IS NOT ZERO. Both counts are always present on a run summary and
    // both are null when the run wrote no structured receipt. "Nothing was
    // recorded" and "the recording says nothing changed" are different facts;
    // the second is worth a line and the first is worth silence.
    receipt: run.files == null && run.commits == null
      ? null
      : { files: run.files, commits: run.commits },
  };
}

function propsFor(input: TicketCardInput, ticket: WorkTicket): TicketCardProp[] {
  const out: TicketCardProp[] = [];
  // `Key` is the app's real handle for a ticket. The design handoff drew
  // `WS-412` and there is no such field on the wire: the store has no counter
  // and an id is an unordered uuid, so the first six hex are what everything
  // else already quotes (lib/tasks.ticketKey).
  out.push({
    key: "key",
    label: "Key",
    scope: scopeName(ticket.scope || input.scope),
    text: ticketKey(ticket.id || input.id),
  });
  // `Blocked by`, never `Blocks`. The inverse edge is not stored, not derived
  // and not sent; this one is the resolved non-terminal subset of `depends_on`.
  const blocked = ticket.blocked_by ?? [];
  if (blocked.length) out.push({ key: "blocked_by", label: "Blocked by", links: blocked });
  const labels = (ticket.labels ?? []).filter((l) => l.trim().length > 0);
  if (labels.length) out.push({ key: "labels", label: "Labels", labels });
  const effort = (ticket.effort || "").trim();
  if (effort) out.push({ key: "effort", label: "Effort", text: effort });
  return out;
}

/** The ONE line under the card, decided in this order.
 *
 *  An agent that stopped to ask outranks everything else the card could say,
 *  because it is the only one of the three that is waiting on the reader. A
 *  fact about the filing that the ticket cannot re-derive comes next. The
 *  pickup warning is last and only while nothing has run: once a run exists,
 *  the question it answers has been answered by events. */
function noteFor(input: TicketCardInput): TicketCardNote | null {
  if (input.waiting) {
    return {
      kind: "waiting",
      itemId: input.waiting.id,
      agent: input.waiting.agent,
      text: `${input.waiting.agent} is waiting on an answer`,
    };
  }
  // The ranking below is deliberate and stays. Joining the filing note to the
  // pickup warning was tried on 2026-08-16 and reverted: both lines are long
  // sentences, so the pair rendered as a ~200-character run-on under a card
  // whose whole point is to be one readable line. When a merge lands in a scope
  // nothing polls, the merge is the more surprising fact and wins the line; the
  // ticket page carries the rest.
  if (input.filedNote) return { kind: "muted", text: input.filedNote };
  if (input.ticket && !input.ticket.run && input.pickup) {
    const warn = pickupNote(input.pickup);
    if (warn) return { kind: "muted", text: warn };
  }
  return null;
}

export function ticketCardPlan(input: TicketCardInput): TicketCardPlan {
  const fallbackTitle = input.filedTitle.trim() || ticketKey(input.id);
  const bare: TicketCardPlan = {
    phase: "cold",
    title: fallbackTitle,
    status: null,
    groups: [],
    body: "",
    props: [],
    run: null,
    actions: [],
    note: null,
    problem: null,
  };

  // Gone. The card keeps its header so the answer still reads, says so in a
  // line, and offers nothing: every action it has would address a 404.
  if (input.deleted) return { ...bare, phase: "deleted" };

  // Not here yet. The id and the scope are known, so Open ticket is honest
  // even before the ticket itself has been read.
  if (!input.ticket) {
    return {
      ...bare,
      phase: input.error ? "unreadable" : "cold",
      actions: ["open"],
      note: noteFor(input),
      problem: input.error,
    };
  }

  const ticket = input.ticket;
  const body = description(ticket).trim();
  const props = propsFor(input, ticket);
  const run = ticket.run ? runFor(ticket.run, input.nowMs) : null;

  const groups: TicketCardGroupKey[] = [];
  if (body) groups.push("task");
  if (props.length) groups.push("ticket");
  if (run) groups.push("run");

  const actions: TicketCardActionId[] = ["open"];
  // Mutually exclusive by construction: `Run now` is offered only to a ticket
  // that has never run, and `View run` only to one that has.
  //
  // `run` being null is NOT the same question as "has this ticket ever run".
  // The card's fallback read (`GET /api/tasks/{scope}/{id}`, TicketCard.tsx)
  // carries no run summary at all, so a ticket reached that way arrives with
  // `run: null` however many times it has run. Gating on that alone offered
  // `Run now` on finished work, which is the one action here that WRITES, and
  // it would have started a second run of something already done. The ticket
  // records its own runs in `job_ids`, so absence of a summary and absence of a
  // run are distinguishable, and only the second earns the button.
  const everRan = (ticket.job_ids?.length ?? 0) > 0;
  if (run) actions.push("viewRun");
  else if (!everRan && input.pickup?.runnable) actions.push("runNow");

  return {
    phase: "ready",
    title: taskTitle(ticket).trim() || fallbackTitle,
    status: {
      label: taskStatusLabel(ticket.status),
      kind: taskStatusKind(ticket.status),
    },
    groups,
    body,
    props,
    run,
    actions,
    note: noteFor(input),
    problem: input.error,
  };
}
