// ONE ROW SHAPE FOR A TICKET, at every width.
//
// This surface used to draw three: a `TicketTable` above 900px, this grouped
// list below it, and a `BoardCard` on the board. Two of those were the same
// list twice, and the table was the shape the design system had already thrown
// out. `.ws-listrow` is "the one row shape for every list in the app: tickets
// and runs today" (operator direction, 2026-08-12); the runs feed still uses
// it, and this brings tickets back onto it. The table is deleted in the same
// commit, with its CSS.
//
// WHY THE TABLE WAS WRONG HERE, specifically. It carried eight columns, and
// four of them are empty on most rows: labels are unset on 95% of the live
// store, effort on 70%, priority on 45%, and assignee is one repeated string
// per installation. So the widest, most rigid shape on the page was spending
// half its width on blanks, while the title, which is the only thing a reader
// scans, was capped at 38%. On a phone the same rows lost the columns
// entirely.
//
// WHY THE GROUPING WENT. The status headings were computed over the CURRENT
// PAGE of 50 rows, so on ~200 tickets sorted by date "Blocked 2" meant two
// blocked tickets on this page, the heading reappeared with a different number
// on the next one, and no number on screen was the count of anything. The
// status bar above the list carries the server's own totals instead, and every
// row now says its own status in words, so the heading was labelling something
// each row already states.
import { Link } from "react-router-dom";
import { StatusDot } from "@workshop/ui";
import {
  taskStatusKind, taskStatusTitle, ticketHref, ticketRowFacts,
  title as taskTitle, type Task,
} from "../../lib/tasks";
import { ago } from "./bits";

// A whole-row link takes the row's ink and no underline: the row shape is the
// affordance, and an underlined title in a list of forty is a list of forty
// underlines. Same constant, same reason, as components/runs/RunRow.
const ROW_LINK = { color: "inherit", textDecoration: "none" } as const;

export function TicketList({
  tasks,
  showScope,
}: {
  tasks: Task[];
  /** Print the scope on each row. Off when the list is already filtered to one
   *  scope (an app's own Tickets tab, or a scope filter), where it would be the
   *  filter repeated on every row. */
  showScope: boolean;
}) {
  return (
    <ul className="ws-list">
      {tasks.map((t) => (
        <li key={`${t.scope}:${t.id}`}>
          <Link
            className="ws-listrow"
            style={ROW_LINK}
            to={ticketHref(t.scope ?? "workshop", t.id)}
          >
            {/* A bare dot. The WORD is the first thing in the meta line below,
                because a colour answers "is this blocked or just queued" only
                for a reader who has already learned the five hues. */}
            <span
              className="ws-listrow__mark"
              title={taskStatusTitle(t.status)}
              aria-label={taskStatusTitle(t.status)}
            >
              <StatusDot status={taskStatusKind(t.status)}>{null}</StatusDot>
            </span>
            <span className="ws-listrow__title" title={taskTitle(t)}>
              {taskTitle(t) || "(untitled)"}
            </span>
            <span className="ws-listrow__meta">
              {/* Which facts, in what order, and which ones drop out is
                  `ticketRowFacts` in lib/tasks, so the rule is executed in a
                  test rather than read off this JSX. */}
              {ticketRowFacts(t, { showScope }).map((fact) => (
                <span key={fact} className="ws-listrow__scope">{fact}</span>
              ))}
              <span className="ws-listrow__time" title={t.updated}>
                {ago(t.updated)}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
