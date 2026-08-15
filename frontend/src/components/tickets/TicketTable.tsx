// The table view. Fixed layout inside the design system's TableScroll, so a
// narrow desktop scrolls the columns rather than crushing the title.
//
// ASSIGNEE IS A COLUMN. It was cut in the first pass of this redesign because
// it is the string "operator" on every ticket in THIS install, which was the
// wrong test: the field varies per deployment (agents are registered at runtime,
// and an install has as many people as it has), so measuring one store's current
// contents and deleting the column is designing for a snapshot. Occupancy is a
// reason to drop a field that is dead by DESIGN, not one that happens to be
// uniform today. It renders muted, and blank when unset.
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
          <col className="ws-tt__c-assignee" />
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
            <th scope="col">Assignee</th>
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
              <td className="ws-tt__muted ws-tt__ellipsis" title={t.assignee ?? ""}>
                {t.assignee ?? ""}
              </td>
              <td className="ws-tt__num ws-tt__time" title={t.updated}>{ago(t.updated)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </TableScroll>
  );
}
