// The drafts-and-parked strip on the ticket list: what it counts, how it asks,
// and what it says.
//
// WHY THE COUNTS ARE THEIR OWN REQUEST. The strip exists so a ticket that burned
// its attempt cap (parked) or a proposal nobody approved (draft) cannot vanish
// behind the default Active view. Counting them off the rows the list just
// fetched defeats that: under any single-status filter, or with "Ready to run"
// checked, the server has already narrowed those rows to zero drafts, so the
// strip silently disappeared exactly when the view was narrow enough to hide the
// work. Under "All statuses" the list is paged (60 rows), so the count came from
// page one and under-reported.
//
// So each count is asked for on its own, with `limit=1`: the endpoint returns
// `total` for the whole filtered set before paging, and one row is the cheapest
// answer that carries it. The scope filter is respected, because a scope-filtered
// view should count within that scope.

import { ALL_SCOPES } from "./workParams";
import { isBacklogOnlyFilter, type StatusFilter } from "./ticketGroups";

// The two statuses that land in the backlog state type and are invisible under
// the Active default. A DRAFT is work the system proposed and nobody agreed to;
// it cannot run, so unsurfaced it is simply lost. A PARKED ticket is this
// codebase's dead letter: `tasks.reclaim` parks a claim that expired at the
// attempt cap, and nothing re-picks it.
export type BacklogStatus = "draft" | "parked";

export type BacklogCounts = Record<BacklogStatus, number>;

export const NO_BACKLOG: BacklogCounts = { draft: 0, parked: 0 };

/** The query for one count probe. `limit=1` because only `total` is read; the
 *  row that comes back is never rendered. */
export function backlogCountQuery(status: BacklogStatus, scope: string): string {
  const params = new URLSearchParams();
  params.set("status", status);
  params.set("limit", "1");
  if (scope && scope !== ALL_SCOPES) params.set("scope", scope);
  return params.toString();
}

export function backlogTotal(counts: BacklogCounts): number {
  return counts.draft + counts.parked;
}

/** Hidden once the operator is already looking at nothing but backlog work, and
 *  hidden when there is none. */
export function showBacklogStrip(counts: BacklogCounts, filter: StatusFilter): boolean {
  return backlogTotal(counts) > 0 && !isBacklogOnlyFilter(filter);
}

export type BacklogStripCopy = { what: string; why: string; verb: string };

/** What the strip says. Two statuses with different reasons for being stuck, so
 *  the sentence names whichever ones are actually present. */
export function backlogStripCopy(counts: BacklogCounts): BacklogStripCopy {
  const { draft, parked } = counts;
  const total = backlogTotal(counts);
  const what = [
    draft > 0 ? `${draft} proposed` : null,
    parked > 0 ? `${parked} parked` : null,
  ].filter(Boolean).join(" and ");
  const why = parked === 0
    ? "Nothing runs them until you approve."
    : draft === 0
      ? "Parked tickets used up their attempts. Nothing retries them on its own."
      : "Nothing runs them until you approve or requeue them.";
  return {
    what: `${what} ${total === 1 ? "ticket is" : "tickets are"} waiting for you`,
    why,
    verb: total === 1 ? "Review it" : "Review them",
  };
}
