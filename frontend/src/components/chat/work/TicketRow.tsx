import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { StatusDot } from "@workshop/ui";
import {
  taskStatusKind, taskStatusLabel, ticketHref, ticketKey, title as taskTitle,
} from "../../../lib/tasks";
import { jobIsLive } from "../../../lib/jobs";
import { formatElapsed } from "../chatScope";
import { elapsedSeconds } from "../turnStatus";
import { AgentAvatar } from "./AgentAvatar";
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
//
// TWO WEIGHTS OF ROW, and 4a is explicit about which is which. An ordinary
// ticket is ONE line and no chrome at all: dot, key, title, and the state word
// pushed right. A row that is happening now, and a row waiting on the operator,
// become bordered cards with a second line naming the agent. The panel is read
// for "what needs me and what is moving", so those two are the only things that
// earn a card, and everything else stays flat behind them.

/** The first line of an inbox item's body, for the card's second line. */
function firstLine(body: string): string {
  return (body || "").split("\n").map((s) => s.trim()).find(Boolean) ?? "";
}

/** What the agent DID, from what kind of item it raised. 4a writes the verb into
 *  the heading ("reviewer asks") rather than putting an icon beside a bare name,
 *  which is what makes the line a sentence. */
const KIND_VERB: Record<InboxItem["kind"], string> = {
  question: "asks",
  review: "proposes",
  notify: "says",
};

/** An agent stopped and is waiting: the one place in the panel that spends a
 *  hue on something other than liveness. A proposal is ordinary work, so it
 *  keeps the accent. */
const KIND_TONE = {
  question: "warn",
  review: "accent",
  notify: "muted",
} as const;

/** Something an agent raised. Selecting it opens the question view. */
export function NeedsYouRow({ item, onSelect }: {
  item: InboxItem;
  onSelect: (id: string) => void;
}) {
  const snippet = firstLine(item.body);
  return (
    <button
      type="button"
      className="chat-workpanel__row chat-workpanel__row--card"
      onClick={() => onSelect(item.id)}
      title={item.title}
    >
      <span className="chat-workpanel__cardline">
        <AgentAvatar name={item.agent} tone={KIND_TONE[item.kind]} />
        <span className="chat-workpanel__asks">
          {item.agent} {KIND_VERB[item.kind] ?? "says"}
        </span>
        <span className="chat-workpanel__spacer" />
        {/* The design's `WS-412`, or its `draft` when an item names no ticket.
            We have neither a ticket counter nor a draft state on an inbox row,
            so this is the six-hex handle everything else quotes, falling back
            to the kind, which is the only other word the row actually holds. */}
        <span className="chat-workpanel__handle">
          {item.ticket_id ? ticketKey(item.ticket_id) : item.kind}
        </span>
        <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="chat-workpanel__caret" />
      </span>
      {/* The question itself, not the title. The title is what it is about and
          the heading above already carries who; this is the ask. */}
      <span className="ws-truncate chat-workpanel__cardtext">{snippet || item.title}</span>
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

  return (
    <Link
      to={ticketHref(ticket.scope || fallbackScope || "workshop", ticket.id)}
      className={`chat-workpanel__row${live ? " chat-workpanel__row--card" : ""}`}
      onClick={onFollow}
      title={name}
    >
      <span className="chat-workpanel__cardline">
        <StatusDot status={taskStatusKind(ticket.status)}>{null}</StatusDot>
        <span className="ws-ticket-key chat-workpanel__handle">{ticketKey(ticket.id)}</span>
        <span className="ws-truncate chat-workpanel__title">{name}</span>
        {/* RIGHT-ALIGNED ON THE TITLE'S OWN LINE. It used to sit on a second
            line under the title, which made every row two lines tall to say one
            word, and pushed the thing the panel is scanned for into the meta
            ink under the thing it describes. */}
        <span className="chat-workpanel__state">{taskStatusLabel(ticket.status)}</span>
      </span>
      {/* ONLY WHILE IT IS GOING. Who is on it and how long they have been is a
          fact with a lifetime; a finished row keeps its state word and nothing
          else, which is what stops the list turning into a log. */}
      {live && (
        <span className="chat-workpanel__cardbody">
          <AgentAvatar name={run?.agent_name} size="sm" />
          <span className="ws-truncate">{run?.agent_name || "an agent"}</span>
          {elapsed && <span className="chat-workpanel__elapsed">{elapsed}</span>}
        </span>
      )}
    </Link>
  );
}
