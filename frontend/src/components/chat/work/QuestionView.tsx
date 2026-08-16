import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button, Input } from "@workshop/ui";
import { runHref } from "../../../lib/jobs";
import { ticketHref } from "../../../lib/tasks";
import { AgentAvatar } from "./AgentAvatar";
import type { ChatWork } from "./useChatWork";
import { TicketRow } from "./TicketRow";
import type { InboxItem } from "./workTypes";

// Answering another agent's question, without leaving the conversation.
//
// THE TYPING RULES ARE THE POINT OF THIS VIEW. A panel with a text field in it,
// opened from a toolbar above a chat composer, is two carets competing for one
// keyboard, and on a phone it is also a keyboard that can eat the screen:
//
//   - The answer field is NEVER autofocused. Opening the panel must not raise a
//     phone keyboard, and on a desktop it must not take the caret off whatever
//     the operator was already typing into the composer.
//   - Enter in the field SENDS THE ANSWER and stops there. It never reaches the
//     chat composer's Enter handling.
//   - Escape closes the whole panel and puts focus back on the Tickets button.
//     Handled by the host (the kit's Popover on a wide screen, WorkPanel's sheet
//     on a narrow one), so it works from inside this field too.
//   - A half-typed answer SURVIVES CLOSING. The text lives in the hook, keyed by
//     item id, never in this component. That is what lets the panel close on an
//     outside click with no confirmation, and it is why switching between two
//     questions with Next does not throw either draft away.
//
// The endpoint is the one that already exists: `POST /inbox/{id}/resolve` takes
// arbitrary free text and writes through to the ticket's review, refusing to
// close the row if that write fails. An offered option is just one of those
// strings, which is why they are buttons rather than a second mechanism.

export default function QuestionView({
  work, items, item, scope, nowMs, onSelect, onBack, onClose, onNavigate,
}: {
  work: ChatWork;
  /** Everything waiting, for `1 of N` and for Next. */
  items: InboxItem[];
  item: InboxItem;
  /** The chat's scope, for a ticket row whose ticket did not carry its own. */
  scope: string | null;
  /** The panel's shared clock, for a ticket whose run is still going. */
  nowMs: number;
  onSelect: (id: string) => void;
  onBack: () => void;
  onClose: () => void;
  onNavigate: (to: string) => void;
}) {
  const index = items.findIndex((i) => i.id === item.id);
  const position = index < 0 ? 0 : index;
  const next = items[(position + 1) % Math.max(1, items.length)];
  const draft = work.drafts[item.id] ?? "";
  const busy = work.busy === item.id;
  // The ticket this is ABOUT, from the shared copy. A question can name a ticket
  // in a scope this chat does not read, in which case there is no row to draw
  // and the footer's Open ticket is the way to it. Half a row would be worse
  // than none.
  const about = item.ticket_id ? work.ticket(item.ticket_id) : undefined;

  // Answering moves on: to the next thing waiting, or back to the list when
  // this was the last one. The row is removed optimistically by the hook, so
  // staying here would leave the view pointing at something that is gone.
  const send = (text: string) => {
    const answer = text.trim();
    if (!answer || busy) return;
    void work.answer(item.id, answer);
    const rest = items.filter((i) => i.id !== item.id);
    if (rest.length) onSelect(rest[0].id);
    else onBack();
  };

  return (
    <>
      <div className="chat-workpanel__head">
        <button type="button" className="chat-workpanel__back" onClick={onBack}>
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden />
          Work
        </button>
        <span className="chat-workpanel__heading">Answer {item.agent}</span>
        {items.length > 1 && (
          <>
            <span className="chat-workpanel__count">{position + 1} of {items.length}</span>
            <button
              type="button"
              className="chat-workpanel__iconbtn"
              onClick={() => onSelect(next.id)}
              title="Next"
              aria-label="Next question"
            >
              <ChevronRight size={14} strokeWidth={1.8} aria-hidden />
            </button>
          </>
        )}
      </div>

      {/* WHAT THIS IS ABOUT, said once. For a blocked ticket the item's title IS
          the ticket's title, so the row says it and repeating it above would be
          the same sentence twice. For an item with no ticket in this payload
          (a promotion proposal, a ticket in a scope this chat does not read) the
          title is the only statement of the subject there is. */}
      {about ? (
        <ul className="chat-workpanel__list chat-workpanel__about">
          <li>
            <TicketRow
              ticket={about}
              fallbackScope={scope}
              nowMs={nowMs}
              onFollow={onClose}
            />
          </li>
        </ul>
      ) : (
        item.title && <p className="chat-workpanel__subject">{item.title}</p>
      )}

      {/* WHO IS ASKING, beside what they asked. The same square the panel's
          needs-you card carries, one size up, so the question reads as coming
          from someone rather than from the surface. */}
      {item.body && (
        <div className="chat-workpanel__ask">
          <AgentAvatar
            name={item.agent}
            tone={item.kind === "question" ? "warn" : "accent"}
            size="lg"
          />
          <p className="chat-workpanel__body">{item.body}</p>
        </div>
      )}

      {item.options.length > 0 && (
        <div className="chat-workpanel__group">
          {/* Sentence case, matching the ticket card. See the note there. */}
          <span className="chat-workpanel__label">Answer</span>
          {/* FULL-WIDTH AND STACKED. An offered answer is a sentence, not a
              word: side by side they truncate at the panel's 360px and the two
              options become unreadable at exactly the moment they are being
              chosen between. */}
          <div className="chat-workpanel__options">
            {item.options.map((o) => (
              <button
                key={o}
                type="button"
                className="chat-workpanel__option"
                disabled={busy}
                onClick={() => send(o)}
              >
                {o}
              </button>
            ))}
          </div>
        </div>
      )}

      <span className="chat-workpanel__label">
        {item.options.length > 0 ? "Or write one" : "Answer"}
      </span>
      <div className="chat-workpanel__write">
        <Input
          value={draft}
          onChange={(e) => work.setDraft(item.id, e.target.value)}
          placeholder="Type an answer"
          aria-label={`Answer ${item.agent}`}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            // The composer is one element away and Enter is its send key. Stop
            // here whether or not there is anything to send: an empty field
            // that let the key through would send the CHAT's draft instead.
            e.preventDefault();
            e.stopPropagation();
            send(draft);
          }}
        />
      </div>
      {/* THE HINT AND THE SEND SHARE A LINE, BELOW THE FIELD. They used to sit
          either side of the input, which put a primary button between the field
          and its own hint and left the field itself about half the panel wide.
          4a gives the field the full measure and hangs the consequence and the
          commit under it. */}
      <div className="chat-workpanel__send">
        <span className="chat-workpanel__note">
          Sends to {item.agent} and unblocks the run.
        </span>
        <Button
          size="sm"
          variant="primary"
          disabled={!draft.trim() || busy}
          onClick={() => send(draft)}
        >
          Send
        </Button>
      </div>

      {/* TEXT-WEIGHT, not four ghost buttons. These are ways out of the view,
          and a rack of outlined rectangles under the one control that commits
          reads as four more things to decide between. */}
      <div className="chat-workpanel__foot chat-workpanel__foot--question">
        {item.ticket_id && (
          <button
            type="button"
            className="chat-workpanel__change"
            onClick={() => { onClose(); onNavigate(ticketHref(item.scope, item.ticket_id!)); }}
          >
            Open ticket
          </button>
        )}
        {item.run_id && (
          <button
            type="button"
            className="chat-workpanel__change"
            onClick={() => { onClose(); onNavigate(runHref(item.run_id!)); }}
          >
            View run
          </button>
        )}
        <span className="chat-workpanel__spacer" />
        {/* Deferral, not closure: the item stays open on the server and comes
            back on its own. */}
        <button
          type="button"
          className="chat-workpanel__change"
          disabled={busy}
          onClick={() => { void work.snooze(item.id, "tomorrow"); onBack(); }}
        >
          Snooze
        </button>
        <button type="button" className="chat-workpanel__change" onClick={onClose}>
          Later
        </button>
      </div>
    </>
  );
}
