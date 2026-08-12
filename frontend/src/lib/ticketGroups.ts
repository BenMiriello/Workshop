// WHICH tickets the list asks the server for. The one rule behind the status
// control, kept out of the page so it can be executed in a test (there is no DOM
// harness; the target is always the rule, not the render).
//
// This file used to also own the list's group-by buckets and a five-lane board.
// Both were deleted on 2026-08-12: grouping by status duplicates the status
// filter, and a board is a surface you drag work across, which nothing here
// does (status changes come from runs). The statuses themselves are still
// derived from the single table in lib/tasks.ts rather than re-listed here.
import {
  STATUSES_BY_STATE_TYPE,
  TASK_STATE_TYPES,
  TASK_STATUSES,
  type TaskStateType,
  type TaskStatus,
} from "./tasks";

// ---- status filter -----------------------------------------------------------
// What the list asks for. The default is ACTIVE (unstarted + started), not
// "everything": that is the convergent default across Linear, Shortcut and
// GitHub, and it is the answer to the question the list is actually asked.
// Backlog, Done and All are each one change of this one control away.
//
// The endpoint takes a comma-separated `status`, so every filter here pushes
// down to it: paging and `total` are the server's for all of them.

export type StatusFilter = "all" | "active" | TaskStateType | TaskStatus;

export const DEFAULT_STATUS_FILTER: StatusFilter = "active";

/** Whether a raw string (a URL param, say) names a filter this list understands. */
export function isStatusFilter(v: string | null | undefined): v is StatusFilter {
  if (!v) return false;
  return v === "all" || v === "active"
    || (TASK_STATE_TYPES as readonly string[]).includes(v)
    || (TASK_STATUSES as readonly string[]).includes(v);
}

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

// The control's menu. FIVE options, not fifteen.
//
// It used to be three optgroups holding every state type and every one of the
// eight statuses. Measured against the live store, five statuses are in use
// (done 110, todo 38, cancelled 28, blocked 2, draft 1) and three have never
// been written, so ten of those options could only ever return nothing. These
// are the views the data supports: what is moving, what is stuck, what has not
// started, what is finished, and everything.
export const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "blocked", label: "Blocked" },
  { value: "backlog", label: "Backlog" },
  { value: "completed", label: "Done" },
  { value: "all", label: "All" },
];
