// How a ticket list is divided up: the pure rules behind the group-by control,
// the board's lanes, and the status filter. Kept out of the page so they can be
// executed in a test (there is no DOM harness; the target is always the rule,
// not the render).
//
// Three questions, deliberately not one:
//
//   groupTickets  — the LIST's optional grouping. Buckets are whatever the data
//                   has, in a fixed order, and an empty bucket does not exist.
//   boardLanes    — the BOARD's columns. Buckets are FIXED and an empty one is
//                   still rendered, because a column that vanishes when it
//                   empties makes the board reshuffle as work moves.
//   status filter — WHICH tickets the list asks the server for at all.
//
// ---- three groupings must agree, and only two of them live here --------------
//
// 1. STATUS_ORDER below: the list's headings, most urgent first.
// 2. The board's lanes: the five state types, in pipeline order.
// 3. `_STATUS_RANK` in backend/services/tasks.py: the rank behind `sort=status`.
//
// The third is in another language in another process and cannot be imported,
// so the first two are DERIVED from the single table in lib/tasks.ts
// (`STATUSES_BY_STATE_TYPE`) and the derivation below reproduces `_STATUS_RANK`
// exactly. Nothing here re-lists the eight statuses by hand. If the backend rank
// changes, change `HEADING_TYPE_ORDER` here; do not hand-write a list next to
// it, which is how the four ad-hoc lanes this file used to carry came about.
import {
  STATUSES_BY_STATE_TYPE,
  TASK_STATE_TYPES,
  TASK_STATUSES,
  scopeLabel,
  taskStateType,
  taskStateTypeLabel,
  taskStateTypeOf,
  taskStatusLabel,
  type Task,
  type TaskStateType,
  type TaskStatus,
} from "./tasks";

export type GroupBy = "none" | "status" | "scope";

export type TicketGroup = {
  key: string;
  label: string;
  tasks: Task[];
};

// Headings read by urgency, not by pipeline position: what is stuck and moving
// first, then what is waiting, then what is finished. This is the ONE place the
// two frontend orders differ, and it is the order the backend's status rank
// already uses.
const HEADING_TYPE_ORDER: TaskStateType[] = [
  "started", "unstarted", "backlog", "completed", "canceled",
];

// blocked, doing, in_review, todo, draft, parked, done, cancelled.
const STATUS_ORDER: TaskStatus[] =
  HEADING_TYPE_ORDER.flatMap((type) => STATUSES_BY_STATE_TYPE[type]);

export function groupTickets(tasks: Task[], by: GroupBy): TicketGroup[] {
  if (by === "none") return [{ key: "all", label: "", tasks }];

  const buckets = new Map<string, Task[]>();
  for (const t of tasks) {
    const key = by === "status" ? String(t.status) : String(t.scope || "workshop");
    const list = buckets.get(key);
    if (list) list.push(t);
    else buckets.set(key, [t]);
  }

  const keys = [...buckets.keys()];
  if (by === "status") {
    // Known statuses in the fixed order, then anything unrecognised, so a status
    // added to the backend before this list shows up rather than disappearing.
    keys.sort((a, b) => {
      const ia = STATUS_ORDER.indexOf(a as TaskStatus);
      const ib = STATUS_ORDER.indexOf(b as TaskStatus);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
    });
  } else {
    keys.sort((a, b) => scopeLabel(a).localeCompare(scopeLabel(b)));
  }

  return keys.map((key) => ({
    key,
    label: by === "status" ? taskStatusLabel(key) : scopeLabel(key),
    tasks: buckets.get(key) ?? [],
  }));
}

// ---- board ------------------------------------------------------------------
// One lane per state type. The lanes used to be four names invented here
// (draft / open / running / done) that existed nowhere else in the system, so
// the board, the backend rank and any future tracker sync each had their own
// idea of what a column was.

export type LaneId = TaskStateType;

export type Lane = {
  id: LaneId;
  label: string;
  // Named so an empty lane can say what belongs in it rather than just "0".
  hint: string;
  tasks: Task[];
};

const LANE_HINT: Record<TaskStateType, string> = {
  backlog: "Proposed or parked. Nothing picks these up.",
  unstarted: "Approved and waiting for a worker.",
  started: "Being worked, in review, or stuck.",
  completed: "Finished.",
  canceled: "Dropped. The work did not happen.",
};

export function boardLanes(tasks: Task[]): Lane[] {
  const byLane = new Map<LaneId, Task[]>(TASK_STATE_TYPES.map((id) => [id, []]));
  for (const t of tasks) byLane.get(taskStateTypeOf(t))?.push(t);
  return TASK_STATE_TYPES.map((id) => ({
    id,
    label: taskStateTypeLabel(id),
    hint: LANE_HINT[id],
    tasks: byLane.get(id) ?? [],
  }));
}

// ---- status filter -----------------------------------------------------------
// What the Open tab asks for. The default is ACTIVE (unstarted + started), not
// "everything": that is the convergent default across Linear, Shortcut and
// GitHub, and it is the answer to the question the list is actually asked.
// Backlog, Done and All are each one change of this one control away.
//
// The endpoint filters by `status`, ONE value, so a filter naming several
// statuses cannot be pushed down to it. `statusFilterQuery` says which of the
// two happens; the page narrows locally when it must, and counts the narrowed
// rows so the "Showing N of M" line stays true.

export type StatusFilter = "all" | "active" | TaskStateType | TaskStatus;

export const DEFAULT_STATUS_FILTER: StatusFilter = "active";

// The state types "Active" stands for. Named once so the label, the filter and
// any future default all mean the same thing.
export const ACTIVE_STATE_TYPES: TaskStateType[] = ["unstarted", "started"];

/** The statuses a filter admits, or null for "every status". */
export function statusFilterStatuses(filter: StatusFilter): TaskStatus[] | null {
  if (filter === "all") return null;
  if (filter === "active") {
    return ACTIVE_STATE_TYPES.flatMap((type) => STATUSES_BY_STATE_TYPE[type]);
  }
  if ((TASK_STATE_TYPES as string[]).includes(filter)) {
    return STATUSES_BY_STATE_TYPE[filter as TaskStateType];
  }
  return [filter as TaskStatus];
}

export type StatusFilterQuery = {
  // The `?status=` value to send, or null for no filter. Comma-separated when
  // the filter admits several statuses.
  //
  // This used to be a SINGLE status plus a `clientSide` flag, because the
  // endpoint took one value: "Active" (four statuses) could not be expressed,
  // so the page fetched the entire cross-scope list unpaged and narrowed it
  // itself. On this box that was a ~400KB payload re-fetched on every sort,
  // scope and search change, for a default view showing 33 rows. The endpoint
  // now accepts a comma-separated list, so every filter pushes down and paging
  // and `total` are the server's again.
  status: string | null;
};

export function statusFilterQuery(filter: StatusFilter): StatusFilterQuery {
  const wanted = statusFilterStatuses(filter);
  if (wanted === null) return { status: null };
  return { status: wanted.join(",") };
}

export function matchesStatusFilter(
  task: { status?: string }, filter: StatusFilter,
): boolean {
  const wanted = statusFilterStatuses(filter);
  return wanted === null || wanted.includes((task.status ?? "") as TaskStatus);
}

/** True when the filter admits nothing but backlog work, i.e. the operator is
 *  already looking at the drafts and parked tickets the strip would offer. */
export function isBacklogOnlyFilter(filter: StatusFilter): boolean {
  const wanted = statusFilterStatuses(filter);
  return !!wanted && wanted.length > 0
    && wanted.every((s) => taskStateType(s) === "backlog");
}

// The control's menu, built from the model so a new status or state type shows
// up without being typed here.
export const STATUS_FILTER_GROUPS: {
  label: string;
  options: { value: StatusFilter; label: string }[];
}[] = [
  {
    label: "Views",
    options: [
      { value: "active", label: "Active" },
      { value: "all", label: "All statuses" },
    ],
  },
  {
    label: "State",
    options: TASK_STATE_TYPES.map((type) => ({
      value: type as StatusFilter, label: taskStateTypeLabel(type),
    })),
  },
  {
    label: "Status",
    options: TASK_STATUSES.map((status) => ({
      value: status as StatusFilter, label: taskStatusLabel(status),
    })),
  },
];
