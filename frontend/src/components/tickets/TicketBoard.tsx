// The board: one lane per status, in pipeline order, cards linking to the
// ticket page.
//
// EACH LANE IS ITS OWN PAGED LIST, and that is the whole reason this component
// fetches rather than being handed rows. A board cannot be filled by one query:
// the server sorts and pages the entire set, so on a queue of any size a single
// `limit` comes back as one status' worth of rows and every other lane renders
// empty while the data says it is full. Asking per lane is also what a lane IS,
// and it makes the heading's count honest (the lane's own `total`) no matter
// how few rows are being shown under it.
//
// Cards do NOT drag. Status changes happen on the ticket, which is where the
// consequences are visible. That is the design's boundary for this pass, not a
// missing half: nothing here is set up for a drag.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Board, BoardCard, BoardColumn, Button, Skeleton, StatusDot, shortHttpError } from "@workshop/ui";
import {
  taskStatusKind, taskStatusTitle, ticketHref, title as taskTitle,
  type Task,
} from "../../lib/tasks";
import {
  BOARD_STATUSES, LANE_SIZE, laneQuery,
  type TicketFilters, type TicketSort,
} from "../../lib/ticketFilters";
import { TagStrip, TicketKey, TicketMeta } from "./bits";

type LaneState = {
  rows: Task[];
  total: number;
  loading: boolean;
  err: string | null;
};

const EMPTY: LaneState = { rows: [], total: 0, loading: true, err: null };

function Lane({
  status, filters, sort,
}: { status: string; filters: TicketFilters; sort: TicketSort }) {
  const [limit, setLimit] = useState(LANE_SIZE);
  const [state, setState] = useState<LaneState>(EMPTY);

  // The filters, as a value the effect can depend on. `filters` is a fresh
  // object every render, so depending on it directly would refetch every lane
  // on every render of the page.
  const key = JSON.stringify([filters, sort]);

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, err: null }));
    try {
      const p = laneQuery(filters, sort, status, limit);
      const r = await fetch(`/api/tasks?${p.toString()}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      setState({ rows: j.tasks ?? [], total: j.total ?? 0, loading: false, err: null });
    } catch (e) {
      setState((s) => ({
        ...s, loading: false, err: e instanceof Error ? e.message : String(e),
      }));
    }
    // `key` stands in for filters+sort by value; status and limit are primitives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, status, limit]);

  useEffect(() => { load(); }, [load]);

  // A new filter puts every lane back to its first page.
  useEffect(() => { setLimit(LANE_SIZE); }, [key]);

  const { rows, total, loading, err } = state;
  const more = rows.length < total;

  return (
    <BoardColumn
      title={taskStatusTitle(status)}
      count={total}
      mark={<StatusDot status={taskStatusKind(status)}>{null}</StatusDot>}
      empty={err ? "Could not load" : loading ? "" : "Nothing here"}
    >
      {loading && rows.length === 0
        ? Array.from({ length: 3 }).map((_, i) => (
            <div className="ws-board__card" key={`s${i}`}><Skeleton width="100%" /></div>
          ))
        : null}
      {rows.map((t) => (
        <BoardCard
          key={`${t.scope}:${t.id}`}
          as={Link}
          to={ticketHref(t.scope ?? "workshop", t.id)}
        >
          {/* The title is one line with an ellipsis, so the full text has to
              stay reachable: `title` is it on a pointer, and the ticket page is
              it everywhere else. */}
          <span className="ws-board__card-title" title={taskTitle(t)}>
            {taskTitle(t) || "(untitled)"}
          </span>
          <span className="ws-board__card-meta">
            <TicketKey id={t.id} />
          </span>
          <span className="ws-board__card-foot">
            <TagStrip labels={t.labels} />
            <TicketMeta task={t} />
          </span>
        </BoardCard>
      ))}
      {/* Per lane, because the lane is the list. The count says how many are
          still unseen rather than only that there are some. */}
      {more && (
        <Button
          size="sm"
          block
          loading={loading}
          onClick={() => setLimit((n) => n + LANE_SIZE)}
        >
          {total - rows.length} more
        </Button>
      )}
    </BoardColumn>
  );
}

export function TicketBoard({
  filters, sort,
}: { filters: TicketFilters; sort: TicketSort }) {
  // The lanes the board draws. An explicit status filter narrows WHICH lanes
  // appear, so filtering to "blocked" gives one column rather than one column
  // with rows and five empty ones.
  const lanes = filters.status.length
    ? BOARD_STATUSES.filter((s) => filters.status.includes(s))
    : BOARD_STATUSES;

  return (
    <Board>
      {lanes.map((status) => (
        <Lane key={status} status={status} filters={filters} sort={sort} />
      ))}
    </Board>
  );
}
