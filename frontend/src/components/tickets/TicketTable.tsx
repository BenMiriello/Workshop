// The table view. Fixed layout inside the design system's TableScroll, so a
// narrow desktop scrolls the columns rather than crushing the title.
//
// SEVEN COLUMNS, NOT THE DESIGN'S EIGHT. The handoff drew an Assignee column;
// `assignee` is the string "operator" on 177 of 177 live tickets, so the column
// could only ever repeat itself down the page. A column that says the same word
// on every row is width spent to say nothing, and this surface was cut back
// once already for exactly that (2026-08-12, measured occupancy). The field is
// untouched in the store and comes back the moment it varies.
//
// The design's "Size" is our `effort` (quick | standard | deep), which is the
// name the store, the API and the ticket page all use.
import { Link } from "react-router-dom";
import { Table, TableScroll, StatusDot } from "@workshop/ui";
import {
  scopeLabel, taskStatusKind, taskStatusLabel, ticketHref, title as taskTitle,
  type Task,
} from "../../lib/tasks";
import { TagStrip, TicketKey, ago } from "./bits";

export function TicketTable({ tasks, showScope }: { tasks: Task[]; showScope: boolean }) {
  return (
    <TableScroll>
      <Table className="ws-ticket-table">
        <colgroup>
          <col className="ws-tt__c-ticket" />
          <col className="ws-tt__c-status" />
          <col className="ws-tt__c-tags" />
          {showScope && <col className="ws-tt__c-scope" />}
          <col className="ws-tt__c-prio" />
          <col className="ws-tt__c-effort" />
          <col className="ws-tt__c-time" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Ticket</th>
            <th scope="col">Status</th>
            <th scope="col">Tags</th>
            {/* Only when the list spans scopes. Filtered to one, the column
                repeats the filter on every row. */}
            {showScope && <th scope="col">Scope</th>}
            <th scope="col" className="ws-tt__num">Priority</th>
            <th scope="col">Effort</th>
            <th scope="col" className="ws-tt__num">Updated</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={`${t.scope}:${t.id}`}>
              <td>
                {/* A link, not a row with an onClick: middle-click opens a tab,
                    the keyboard reaches it, and a phone gives it the native tap
                    treatment. It stretches over the row so the whole row is the
                    target (see .ws-ticket-table td:first-child a). */}
                <Link className="ws-tt__ticket" to={ticketHref(t.scope ?? "workshop", t.id)}>
                  <TicketKey id={t.id} />
                  <span className="ws-tt__title">{taskTitle(t) || "(untitled)"}</span>
                </Link>
              </td>
              <td>
                <StatusDot status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusDot>
              </td>
              <td><TagStrip labels={t.labels} /></td>
              {showScope && <td className="ws-tt__muted">{scopeLabel(t.scope)}</td>}
              <td className="ws-tt__num">
                {typeof t.priority === "number" && t.priority !== 0 ? t.priority : ""}
              </td>
              <td className="ws-tt__muted">{t.effort ?? ""}</td>
              <td className="ws-tt__num ws-tt__time" title={t.updated}>{ago(t.updated)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </TableScroll>
  );
}
