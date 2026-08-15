// The board: one lane per status, in pipeline order, cards linking to the
// ticket page.
//
// Cards do NOT drag. Status changes happen on the ticket, which is where the
// consequences are visible (a run can start from `todo`, readiness is advice
// about handing it over). That is the design's own boundary for this pass, not
// a missing half: nothing here is set up for a drag, so nothing has to be
// undone to add one.
import { Link } from "react-router-dom";
import { Board, BoardCard, BoardColumn, StatusDot } from "@workshop/ui";
import {
  taskStatusKind, taskStatusTitle, ticketHref, title as taskTitle,
  type Task,
} from "../../lib/tasks";
import { boardLanes } from "../../lib/ticketFilters";
import { TagStrip, TicketKey, TicketMeta } from "./bits";

export function TicketBoard({ tasks }: { tasks: Task[] }) {
  const lanes = boardLanes(tasks);
  return (
    <Board>
      {lanes.map((lane) => (
        <BoardColumn
          key={lane.status}
          title={taskStatusTitle(lane.status)}
          count={lane.items.length}
          mark={<StatusDot status={taskStatusKind(lane.status)}>{null}</StatusDot>}
          empty="Nothing here"
        >
          {lane.items.map((t) => (
            <BoardCard
              key={`${t.scope}:${t.id}`}
              as={Link}
              to={ticketHref(t.scope ?? "workshop", t.id)}
            >
              <span className="ws-board__card-title">{taskTitle(t) || "(untitled)"}</span>
              <span className="ws-board__card-meta">
                <TicketKey id={t.id} />
              </span>
              <span className="ws-board__card-foot">
                <TagStrip labels={t.labels} />
                <TicketMeta task={t} />
              </span>
            </BoardCard>
          ))}
        </BoardColumn>
      ))}
    </Board>
  );
}
