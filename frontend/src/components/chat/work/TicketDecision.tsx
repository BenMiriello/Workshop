import { useContext, useState } from "react";
import { Check, ClipboardCheck, Undo2 } from "lucide-react";
import { Button, Textarea } from "@workshop/ui";
import { ChatWorkContext } from "./TicketCard";
import { decisionView } from "./ticketDecision";

// A ticket waiting on the operator, decided WHERE THE MESSAGE IS.
//
// WHAT THIS REPLACES. The backend posts a message into the chat that filed the
// ticket, and that message used to end "Answer here, or on the ticket:
// /tickets/<scope>/<id>". Both halves were wrong: nothing routes a chat reply
// back to a ticket, and a bare path is not a link at all, which on a phone means
// it cannot be followed. The operator had to find the toolbar's Tickets panel,
// where the only control was a free-text box whose answer was then read as the
// wrong verb (see `services/task_decision`).
//
// The pattern is the one `ApprovalBlock` already states for tool permissions:
// rendered inline in the log, under the message that caused it, not in the strip
// above the transcript, which is for things pointing OUT of the chat. A ticket
// this chat filed is not pointing out of the chat. It is a decision to make.
//
// Answering anywhere resolves everywhere: the view is derived from the chat's
// one work payload rather than from what this control remembers clicking. The
// rule is in `ticketDecision.ts` and is tested there.

export default function TicketDecision({ ticketId }: { ticketId: string | null | undefined }) {
  const access = useContext(ChatWorkContext);
  const [sending, setSending] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [writingNote, setWritingNote] = useState(false);

  const view = decisionView({
    ticketId,
    cold: access?.cold ?? true,
    inbox: access?.inbox ?? [],
    status: ticketId ? access?.ticket(ticketId)?.status : undefined,
  });

  if (!access || view.kind === "hidden") return null;

  if (view.kind === "resolved") {
    // History now, and history should not shout. Same treatment a resolved
    // permission gets: one quiet line, kept in place so the transcript still
    // reads as what happened.
    return (
      <div className="chat-approval is-resolved">
        <Check size={14} aria-hidden className="chat-approval__icon" />
        <span className="chat-approval__resolved">{view.said}</span>
      </div>
    );
  }

  const send = async (text: string, key: string) => {
    setSending(key);
    try {
      await access.answer(view.itemId, text);
    } finally {
      setSending(null);
    }
  };

  // A stuck ticket wants prose, and may want a grant or a standing rule with it.
  // The panel already knows how to offer those, so this hands over rather than
  // building a second, thinner version of the same surface.
  if (!view.finished) {
    return (
      <div className="chat-approval">
        <div className="chat-approval__head">
          <ClipboardCheck size={14} aria-hidden className="chat-approval__icon" />
          <span className="chat-approval__title">This ticket stopped and needs you</span>
        </div>
        <div className="chat-approval__actions">
          <Button size="sm" variant="primary" onClick={() => access.openQuestion(view.itemId)}>
            Answer it
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-approval">
      <div className="chat-approval__head">
        <ClipboardCheck size={14} aria-hidden className="chat-approval__icon" />
        <span className="chat-approval__title">The work is finished and waiting on you</span>
      </div>

      {writingNote && (
        <Textarea
          className="chat-approval__reason"
          value={note}
          rows={2}
          autoFocus
          // The next attempt READS this. It is written into the ticket body,
          // which is all a worker is handed, so a bare refusal leaves it
          // guessing what was wrong.
          placeholder="What should change? This is written into the ticket, so the next attempt reads it."
          onChange={(e) => setNote(e.target.value)}
        />
      )}

      <div className="chat-approval__actions">
        <Button
          size="sm"
          variant="primary"
          loading={sending === "accept"}
          disabled={sending != null}
          onClick={() => void send("Accept", "accept")}
        >
          <Check aria-hidden /> Accept
        </Button>
        {writingNote ? (
          <Button
            size="sm"
            loading={sending === "back"}
            disabled={sending != null}
            onClick={() => void send(note.trim() || "Send back", "back")}
          >
            <Undo2 aria-hidden /> Send it back
          </Button>
        ) : (
          <Button size="sm" disabled={sending != null} onClick={() => setWritingNote(true)}>
            <Undo2 aria-hidden /> Send back
          </Button>
        )}
      </div>
      <p className="chat-approval__detail">
        Accept marks it done. Sending it back returns it to the queue with your note.
      </p>
    </div>
  );
}
