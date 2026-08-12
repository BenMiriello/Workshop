import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { SectionLabel, StatusDot } from "@workshop/ui";
import { usePoll } from "../../lib/usePoll";
import { activeSlice, scopeTicketsUrl } from "../../lib/activeTickets";
import {
  ticketHref, taskStatusKind, taskStatusLabel, title as taskTitle, type Task,
} from "../../lib/tasks";
import ChatProblemBlock from "./ChatProblemBlock";
import InboxStrip, { useInboxItems, type Inbox } from "./InboxStrip";

// THE CHAT'S ONE WORK DRAWER. Two zones, two questions:
//
//   Needs you    what wants the operator, right now, in this conversation
//   Active here  what exists in this scope's queue and is under way
//
// They are not the same list and neither is redundant with the other. What was
// redundant was the SECOND ticket list: the toolbar used to carry a bell for the
// first question and a "Tasks" drawer for the second, six pixels apart under
// two names that are synonyms, and the "Tasks" drawer was a full inline editor
// for eleven fields sitting in a 40vh overlay on top of the transcript.
//
// THE BADGE COUNTS THE FIRST ZONE ONLY. An open-ticket count is ambient
// information wearing an alarm's clothing; a badge is an alarm. Forty open
// tickets is the normal state of a working queue and a permanent "40" on the
// toolbar teaches the operator to stop reading it.
//
// THE SECOND ZONE IS READ-ONLY, and that is a deliberate loss of nothing:
// TicketDetail now edits every field the old drawer could (priority, effort,
// assignee, acceptance, max_attempts, labels, depends_on, relates_to; see
// lib/ticketEdit.ts), so a row here is a link to the place that edits it. The
// authoring surface that used to live here also still exists untouched for
// agent scopes: `TaskPanel` keeps serving the Agent detail page, with create,
// delete, Approve and the blocked-review verbs. The chat simply stopped using
// it.
//
// MOUNTED WHILE CLOSED. This component renders nothing when `open` is false but
// keeps running `useInboxItems`, so the toolbar badge is live without the
// operator opening anything to find out whether it should be. Only the ticket
// read is gated on `open`: nothing upstream needs its count.

// The queue is unbounded and the drawer is not. The rest is one tap away on the
// full list rather than a scroll with no natural end, the same rule the inbox
// zone applies to its own rows.
const MAX_ROWS = 6;

// Tickets move when a run moves, which is slower than an inbox item arriving
// and only worth watching while the drawer is actually on screen.
const POLL_MS = 15000;

function ActiveHere({
  scope, rows, err, reload, onOpenAll, onFollow,
}: {
  scope: string;
  rows: Task[] | null;
  err: string | null;
  reload: () => void;
  onOpenAll: () => void;
  onFollow: () => void;
}) {
  const slice = activeSlice(rows, MAX_ROWS);
  const endpoint = scopeTicketsUrl(scope);

  return (
    <>
      {/* The failure is stated ABOVE whatever is on screen, loaded rows or not.
          A failed poll leaves the last good list showing (blanking it on a
          network blip is worse than being briefly stale), so the error cannot
          live inside the empty branch: that is the one case where it would
          never be seen. It names the endpoint and offers the retry, like every
          other error state in the chat. */}
      {err && (
        <ChatProblemBlock
          problem={{ text: `Couldn't load tickets: ${err}`, endpoint, retry: reload }}
        />
      )}
      {rows === null ? (
        !err && <div className="chat-inbox__empty">Loading…</div>
      ) : slice.total === 0 ? (
        <div className="chat-inbox__empty">
          Nothing under way in this scope. File work as a ticket and an agent can pick it up.
        </div>
      ) : (
        <ul className="chat-inbox__list">
          {slice.rows.map((t) => {
            const name = taskTitle(t) || t.id;
            return (
              <li key={t.id} className="chat-inbox__item">
                {/* A real link, not a button that navigates: the operator can
                    open a ticket in a new tab from here like anywhere else. */}
                <Link
                  to={ticketHref(t.scope || scope, t.id)}
                  className="ws-ticket-link"
                  onClick={onFollow}
                >
                  <StatusDot status={taskStatusKind(t.status)}>
                    {taskStatusLabel(t.status)}
                  </StatusDot>
                  <span className="ws-truncate ws-ticket-link__title" title={name}>{name}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <button type="button" className="chat-inbox__more" onClick={onOpenAll}>
        {slice.overflow > 0 ? `Open all (${slice.overflow} more)` : "Open all"}
        <ChevronRight size={13} strokeWidth={1.8} aria-hidden />
      </button>
    </>
  );
}

export default function WorkDrawer({
  chatId,
  scope,
  open,
  refetchKey = 0,
  onCountChange,
  onClose,
  onNavigate,
}: {
  /** The open thread, or null on a chat that has not been created yet. */
  chatId: string | null;
  /** The task-store scope this chat maps to, or null when it has no queue. */
  scope: string | null;
  open: boolean;
  /** Bumped when a turn ends, so work the turn filed shows without a refresh. */
  refetchKey?: number;
  /** Needs-you count, for the toolbar badge. */
  onCountChange: (n: number) => void;
  onClose: () => void;
  onNavigate: (to: string) => void;
}) {
  const inbox: Inbox = useInboxItems(chatId);
  const [rows, setRows] = useState<Task[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { onCountChange(inbox.total); }, [inbox.total, onCountChange]);

  const loadTickets = useCallback(async () => {
    if (!scope) { setRows(null); return; }
    try {
      const r = await fetch(scopeTicketsUrl(scope));
      if (!r.ok) { setErr(`${r.status} ${await r.text().catch(() => "")}`.trim()); return; }
      const j = (await r.json()) as { tasks?: Task[] };
      setErr(null);
      setRows(j.tasks ?? []);
    } catch (e) {
      // Same rule as the inbox zone: a failed poll leaves the last good list on
      // screen rather than blanking it.
      setErr(String(e));
    }
  }, [scope]);

  // Only while the drawer is on screen. usePoll reads `fn` through a ref, so a
  // scope change or a finished turn cannot reach it; the effect below owns
  // those, and `immediate: false` keeps the two from double-fetching on open.
  usePoll(loadTickets, POLL_MS, { enabled: open && !!scope, immediate: false });
  useEffect(() => {
    if (!open) return;
    void loadTickets();
  }, [open, loadTickets, refetchKey]);

  // Escape closes, matching every other transient panel in the app.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const reload = useCallback(() => { void loadTickets(); }, [loadTickets]);
  const openGlobalInbox = useCallback(() => onNavigate("/work?tab=inbox"), [onNavigate]);
  const openAllTickets = useCallback(
    () => onNavigate(scope ? `/work?scope=${encodeURIComponent(scope)}` : "/work"),
    [onNavigate, scope],
  );

  // Closed is the normal state: the inbox poll above is still running and the
  // badge is still live, and this renders nothing.
  if (!open) return null;

  return (
    <div className="chat-inbox" role="region" aria-label="Tickets">
      {chatId && (
        <>
          <SectionLabel>Needs you</SectionLabel>
          <InboxStrip inbox={inbox} onOpenGlobal={openGlobalInbox} />
        </>
      )}
      {scope && (
        <>
          <SectionLabel>Active here</SectionLabel>
          <ActiveHere
            scope={scope}
            rows={rows}
            err={err}
            reload={reload}
            onOpenAll={openAllTickets}
            onFollow={onClose}
          />
        </>
      )}
    </div>
  );
}
