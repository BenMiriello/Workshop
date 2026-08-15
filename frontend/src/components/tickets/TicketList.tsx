// The narrow-viewport list: rows grouped under a status heading.
//
// This is what BOTH desktop views become below 900px. The handoff offered two
// mobile shapes and asked for one to ship: this one, not the status pager. A
// pager puts five of the six statuses behind a horizontal swipe, so "what is
// blocked" costs a gesture and a guess about which lane it is in, and the
// answer to "what is going on" is never on screen at once. A grouped list is
// the same information in one vertical scroll, which is the gesture a phone is
// already doing.
//
// Empty groups are dropped here and kept on the board: an empty lane on a board
// is the slot that says the lane exists, and an empty heading on a phone is a
// row of chrome between the reader and the next real row.
import { Link } from "react-router-dom";
import { StatusDot } from "@workshop/ui";
import {
  taskStatusKind, taskStatusTitle, ticketHref, title as taskTitle, type Task,
} from "../../lib/tasks";
import { listGroups } from "../../lib/ticketFilters";
import { TagStrip, TicketKey, TicketMeta, ago } from "./bits";

export function TicketList({ tasks }: { tasks: Task[] }) {
  const groups = listGroups(tasks);
  return (
    <div className="ws-ticketlist">
      {groups.map((g) => (
        <section key={g.status} className="ws-ticketlist__group">
          <header className="ws-ticketlist__head">
            <StatusDot status={taskStatusKind(g.status)}>{null}</StatusDot>
            <span className="ws-ticketlist__label">{taskStatusTitle(g.status)}</span>
            <span className="ws-ticketlist__count">{g.items.length}</span>
          </header>
          <ul className="ws-list">
            {g.items.map((t) => (
              <li key={`${t.scope}:${t.id}`}>
                <Link
                  className="ws-ticketrow"
                  to={ticketHref(t.scope ?? "workshop", t.id)}
                >
                  <span className="ws-ticketrow__title">{taskTitle(t) || "(untitled)"}</span>
                  <span className="ws-ticketrow__meta">
                    <TicketKey id={t.id} />
                    <TagStrip labels={t.labels} />
                    <TicketMeta task={t} />
                    <span className="ws-ticketrow__time" title={t.updated}>{ago(t.updated)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
