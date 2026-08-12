// How a ticket list is divided up: the pure rule behind the group-by control and
// the board's lanes. Kept out of the page so it can be executed in a test (there
// is no DOM harness; the target is always the rule, not the render).
//
// Two different questions, deliberately not one:
//
//   groupTickets  — the LIST's optional grouping. Buckets are whatever the data
//                   has, in a fixed order, and an empty bucket does not exist.
//   boardLanes    — the BOARD's columns. Buckets are FIXED and an empty one is
//                   still rendered, because a column that vanishes when it
//                   empties makes the board reshuffle as work moves.
//
// The board folds eight statuses into four lanes on purpose. Three of the eight
// are what live data actually uses, and eight columns of which five are
// permanently empty is not a view, it is a wall.
import { scopeLabel, taskStatusLabel, type Task, type TaskStatus } from "./tasks";

export type GroupBy = "none" | "status" | "scope";

export type TicketGroup = {
  key: string;
  label: string;
  tasks: Task[];
};

// The order buckets appear in when grouping by status: what is stuck, then what
// is moving, then what is waiting, then what is finished. This is the same
// intent as the backend's status sort rank, expressed for headings.
const STATUS_ORDER: TaskStatus[] = [
  "blocked", "doing", "in_review", "todo", "draft", "parked", "done", "cancelled",
];

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

export type LaneId = "draft" | "open" | "running" | "done";

export type Lane = {
  id: LaneId;
  label: string;
  // Named so an empty lane can say what belongs in it rather than just "0".
  hint: string;
  tasks: Task[];
};

// Which lane each status lands in. `blocked` and `parked` sit in Open because
// they are work that has not happened yet and still needs the operator; putting
// them in their own lanes is what produced five empty columns.
const LANE_OF: Record<TaskStatus, LaneId> = {
  draft: "draft",
  todo: "open",
  blocked: "open",
  parked: "open",
  doing: "running",
  in_review: "running",
  done: "done",
  cancelled: "done",
};

const LANE_META: { id: LaneId; label: string; hint: string }[] = [
  { id: "draft", label: "Draft", hint: "Proposed, not approved yet." },
  { id: "open", label: "Open", hint: "Approved and waiting, including blocked." },
  { id: "running", label: "Running", hint: "Being worked, or waiting on review." },
  { id: "done", label: "Done", hint: "Finished or cancelled." },
];

export const laneFor = (status?: string): LaneId =>
  LANE_OF[(status ?? "") as TaskStatus] ?? "open";

export function boardLanes(tasks: Task[]): Lane[] {
  const byLane = new Map<LaneId, Task[]>(LANE_META.map((m) => [m.id, []]));
  for (const t of tasks) byLane.get(laneFor(t.status))?.push(t);
  return LANE_META.map((m) => ({ ...m, tasks: byLane.get(m.id) ?? [] }));
}
