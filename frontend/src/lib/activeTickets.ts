// "Active here": which of a scope's tickets the chat's work drawer shows, and
// the one read it makes to get them.
//
// NO SECOND STATUS TABLE. The status -> state_type mapping and the definition
// of "active" both live where every other surface reads them: `taskStateTypeOf`
// in lib/tasks and `ACTIVE_STATE_TYPES` in lib/ticketGroups, which mirror
// backend `services/tasks.py _STATE_TYPE`. A fourth copy here is exactly the
// drift the consolidation plan calls out (three groupings that must agree).
// What is genuinely this drawer's own is the cap and the request.
import { taskStateTypeOf, type Task } from "./tasks";
import { ACTIVE_STATE_TYPES } from "./ticketGroups";

// `sort=queue` is the scheduler's own order (`-priority, created ASC`), so the
// top of the list is the work that would actually be picked up next. The
// filtering to active happens client-side: the endpoint's `include_done`
// defaults to true and it has no `state_type=` filter.
export function scopeTicketsUrl(scope: string): string {
  return `/api/tasks?scope=${encodeURIComponent(scope)}&sort=queue`;
}

export function isActiveTicket(row: { status?: string; state_type?: string }): boolean {
  return ACTIVE_STATE_TYPES.includes(taskStateTypeOf(row));
}

export type ActiveSlice = {
  // The rows to render, capped at `max`.
  rows: Task[];
  // How many are active in total, before the cap.
  total: number;
  // How many the cap left out. The drawer names this on its "Open all" link
  // rather than growing a scrollable list with no natural end.
  overflow: number;
};

// The active rows, in the order the server sent them, capped. The drawer is a
// glance at what is under way, not the ticket list: unbounded belongs on /work.
export function activeSlice(rows: Task[] | null | undefined, max: number): ActiveSlice {
  const active = (rows ?? []).filter(isActiveTicket);
  const cap = Math.max(0, max);
  return {
    rows: active.slice(0, cap),
    total: active.length,
    overflow: Math.max(0, active.length - cap),
  };
}
