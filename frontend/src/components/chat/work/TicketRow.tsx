import { Link } from "react-router-dom";
import { ChevronRight, MessageCircleQuestion, ClipboardCheck, Info } from "lucide-react";
import { StatusDot } from "@workshop/ui";
import {
  taskStatusKind, taskStatusLabel, ticketHref, ticketKey, title as taskTitle,
} from "../../../lib/tasks";
import { jobIsLive } from "../../../lib/jobs";
import { ago } from "../../tickets/bits";
import { formatElapsed } from "../chatScope";
import { elapsedSeconds } from "../turnStatus";
import type { InboxItem, WorkTicket } from "./workTypes";

// The two rows the Work panel is made of, and the only two it has.
//
// They are in their own file because the ticket card (package D) renders the
// same ticket row inside an answer: one row idiom, so a ticket in the panel and
// the same ticket in the transcript cannot end up looking like two things.
//
// A ROW IS A LINK, not a button that navigates. That is what gives it
// middle-click, "open in new tab" and keyboard focus, and it is the rule the
// drawer this replaces already followed. Row links carry no underline: the
// whole line is the target and hierarchy comes from the hover background, which
// is how `.ws-ticket-link` has always worked.

/** The first line of an inbox item's body, for the row's second line. */
function firstLine(body: string): string {
  return (body || "").split("\n").map((s) => s.trim()).find(Boolean) ?? "";
}

const KIND_ICON = {
  question: MessageCircleQuestion,
  review: ClipboardCheck,
  notify: Info,
} as const;

/** Something an agent raised. Selecting it opens the question view. */
export function NeedsYouRow({ item, onSelect }: {
  item: InboxItem;
  onSelect: (id: string) => void;
}) {
  const Icon = KIND_ICON[item.kind] ?? Info;
  const snippet = firstLine(item.body);
  return (
    <button
      type="button"
      className="chat-work__row"
      onClick={() => onSelect(item.id)}
      title={item.title}
    >
      <Icon size={14} strokeWidth={1.8} aria-hidden className="chat-work__icon" />
      <span className="chat-work__rowbody">
        <span className="chat-work__line">
          <span className="chat-work__agent">{item.agent}</span>
          <span className="ws-truncate chat-work__title">{item.title}</span>
        </span>
        {snippet && <span className="ws-truncate chat-work__meta">{snippet}</span>}
      </span>
      <ChevronRight size={14} aria-hidden className="chat-work__caret" />
    </button>
  );
}

/** One ticket. The dot, the key, the title, and the state word as PLAIN TEXT.
 *
 *  The word is not decoration on the dot. A colour answers "is this blocked or
 *  just queued" only for someone who has already learned the five hues, and
 *  that is exactly what a ticket list is read for (frontend/CLAUDE.md, "A record
 *  page is not a list page"). */
export function TicketRow({ ticket, fallbackScope, nowMs, onFollow }: {
  ticket: WorkTicket;
  /** The chat's scope, for a row whose ticket did not carry its own. */
  fallbackScope: string | null;
  /** Ticks once a second while any row is live. Frozen otherwise. */
  nowMs: number;
  onFollow: () => void;
}) {
  const name = taskTitle(ticket) || ticket.id;
  const run = ticket.run;
  const live = jobIsLive(run?.status);
  const startedMs = run?.started_at ? Date.parse(run.started_at) : NaN;
  const elapsed = live ? formatElapsed(elapsedSeconds(startedMs, nowMs)) : "";
  // The ticket's state, then who is on it, then when it last moved. Only the
  // clock is a hue, and only while it is still going.
  const meta = [
    taskStatusLabel(ticket.status),
    run?.agent_name || null,
    live ? null : ago(ticket.updated),
  ].filter(Boolean);

  return (
    <Link
      to={ticketHref(ticket.scope || fallbackScope || "workshop", ticket.id)}
      className="chat-work__row"
      onClick={onFollow}
      title={name}
    >
      <StatusDot status={taskStatusKind(ticket.status)}>{null}</StatusDot>
      <span className="chat-work__rowbody">
        <span className="chat-work__line">
          <span className="ws-ticket-key">{ticketKey(ticket.id)}</span>
          <span className="ws-truncate chat-work__title">{name}</span>
        </span>
        <span className="chat-work__meta">
          {meta.join(" · ")}
          {elapsed && <span className="chat-work__elapsed">{elapsed}</span>}
        </span>
      </span>
    </Link>
  );
}
