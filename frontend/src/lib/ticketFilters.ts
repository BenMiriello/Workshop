// WHAT the tickets list is asking for: the filter set, the sort, the view, and
// the round trip between those and the URL. No DOM, no fetch — the page renders
// this, it does not decide it, so the rules below are executed in
// ticketFilters.test.ts instead of being eyeballed on screen.
//
// This replaces the single-select `StatusFilter` model (lib/ticketGroups.ts),
// which could express one bucket at a time. The redesigned bar has a chip per
// active filter and multi-value chips ("Status · 2"), which a single select
// cannot say. `ticketGroups` still owns the status VOCABULARY that the presets
// below are built from; it is not re-listed here.
import {
  STATUSES_BY_STATE_TYPE,
  TASK_STATUSES,
  type TaskSort,
  type TaskStatus,
} from "./tasks";
import { ACTIVE_STATE_TYPES } from "./ticketGroups";

// ---- views -------------------------------------------------------------------
// Timeline is in the switcher and disabled, so the control does not have to be
// redesigned when it lands (design handoff 2026-08-14). It is deliberately NOT
// a value this module will parse: a `?view=timeline` URL falls back to the
// default rather than rendering a blank page.
export type TicketView = "table" | "board";

export const TICKET_VIEWS: TicketView[] = ["table", "board"];

export const DEFAULT_VIEW: TicketView = "table";

export const isTicketView = (v: string | null | undefined): v is TicketView =>
  !!v && (TICKET_VIEWS as string[]).includes(v);

// ---- the board's lanes -------------------------------------------------------
// In pipeline order, left to right is progress. `parked` and `cancelled` are
// not lanes: they are where work goes to STOP, and a board is a picture of work
// in motion. Both remain reachable through the status filter.
export const BOARD_STATUSES: TaskStatus[] = [
  "draft", "todo", "doing", "in_review", "blocked", "done",
];

// ---- the default status set --------------------------------------------------
// It depends on the view, and that is not a shortcut. A TABLE answering "what
// is in flight" must not open with 108 done tickets (measured on the live
// store: 108 of 177). A BOARD whose lanes are the statuses must not open with
// two permanently empty ones, which is what the table's default would do to it.
//
// So each view opens on the set it can actually draw, and an EXPLICIT status
// filter is honoured by both. The difference is only ever the default.
export const ACTIVE_STATUSES: TaskStatus[] =
  ACTIVE_STATE_TYPES.flatMap((t) => STATUSES_BY_STATE_TYPE[t]);

export function defaultStatuses(view: TicketView): TaskStatus[] {
  return view === "board" ? BOARD_STATUSES : ACTIVE_STATUSES;
}

// ---- the filter set ----------------------------------------------------------
export type TicketFilters = {
  /** Free text over title and body. Not a chip: it has its own field. */
  q: string;
  /** Empty means every status, which is what clearing the chip does. */
  status: TaskStatus[];
  /** null means every scope. */
  scope: string | null;
  /** A row matches if it carries ANY of these. */
  labels: string[];
  effort: string[];
};

export type TicketSort = { key: TaskSort; dir: "asc" | "desc" };

export const DEFAULT_SORT: TicketSort = { key: "updated", dir: "desc" };

/** The filter set a view opens on, before the URL says otherwise. */
export function defaultFilters(view: TicketView): TicketFilters {
  return { q: "", status: defaultStatuses(view), scope: null, labels: [], effort: [] };
}

// Two sets, same members, order-independent. Used to decide whether a status
// filter is still the default (and so whether its chip reads "Active" or lists
// what was picked).
const sameSet = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && [...a].sort().join() === [...b].sort().join();

export const isDefaultStatus = (f: TicketFilters, view: TicketView): boolean =>
  sameSet(f.status, defaultStatuses(view));

/** Is anything narrowing the list beyond the view's own default? */
export function isNarrowed(f: TicketFilters, view: TicketView): boolean {
  return !!f.q || !!f.scope || f.labels.length > 0 || f.effort.length > 0
    || !isDefaultStatus(f, view);
}

// ---- URL round trip ----------------------------------------------------------
// `view`, `scope` and the filters live in the URL so a filtered list is a link
// (frontend/CLAUDE.md: view, scope and tab belong in the URL). The DEFAULT is
// spelled by leaving a param OFF, so an unfiltered list is a plain `/work` and
// the back button walks real states rather than a trail of equivalent URLs.
//
// `status=all` is how "every status" is written, because an empty param would
// be indistinguishable from an absent one, and absent means the default.
const ALL = "all";

const csv = (raw: string | null): string[] =>
  (raw ?? "").split(",").map((s) => s.trim()).filter(Boolean);

export function parseView(params: URLSearchParams): TicketView {
  const v = params.get("view");
  return isTicketView(v) ? v : DEFAULT_VIEW;
}

export function parseSort(params: URLSearchParams): TicketSort {
  const key = params.get("sort");
  const dir = params.get("dir");
  return {
    key: (["updated", "created", "priority", "title", "status", "queue"] as const)
      .includes((key ?? "") as TaskSort) ? (key as TaskSort) : DEFAULT_SORT.key,
    dir: dir === "asc" ? "asc" : "desc",
  };
}

export function parseFilters(params: URLSearchParams, view: TicketView): TicketFilters {
  const rawStatus = params.get("status");
  let status: TaskStatus[];
  if (rawStatus === ALL) status = [];
  else if (rawStatus) {
    // An unrecognised status is dropped rather than shown as a filter nothing
    // can match. If that empties the list, fall back to the default: a typo
    // must not read as "no tickets".
    const known = csv(rawStatus).filter((s) =>
      (TASK_STATUSES as string[]).includes(s)) as TaskStatus[];
    status = known.length ? known : defaultStatuses(view);
  } else status = defaultStatuses(view);

  return {
    q: (params.get("q") ?? "").trim(),
    status,
    scope: params.get("scope") || null,
    labels: csv(params.get("tag")),
    effort: csv(params.get("effort")),
  };
}

/** The next URL for a filter/sort/view change, dropping anything at its default. */
export function writeParams(
  filters: TicketFilters, sort: TicketSort, view: TicketView,
  base?: URLSearchParams,
): URLSearchParams {
  // Anything this module does not own (a deep link's own params) survives.
  const p = new URLSearchParams(base ?? undefined);
  const set = (k: string, v: string | null) => (v ? p.set(k, v) : p.delete(k));

  set("view", view === DEFAULT_VIEW ? null : view);
  set("q", filters.q.trim() || null);
  set("scope", filters.scope);
  set("tag", filters.labels.join(",") || null);
  set("effort", filters.effort.join(",") || null);
  set("status", isDefaultStatus(filters, view)
    ? null
    : (filters.status.length === 0 ? ALL : filters.status.join(",")));
  set("sort", sort.key === DEFAULT_SORT.key ? null : sort.key);
  set("dir", sort.dir === DEFAULT_SORT.dir ? null : sort.dir);
  return p;
}

// ---- the API query -----------------------------------------------------------
// Every filter here pushes DOWN to GET /api/tasks, which is what keeps `total`
// and paging honest: a filter narrowed in the browser reports the count of the
// unnarrowed set and pages against it, so the number beside the title
// contradicts the rows beneath it.
export function apiQuery(
  filters: TicketFilters, sort: TicketSort, limit: number,
): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.status.length) p.set("status", filters.status.join(","));
  if (filters.scope) p.set("scope", filters.scope);
  if (filters.labels.length) p.set("labels", filters.labels.join(","));
  if (filters.effort.length) p.set("effort", filters.effort.join(","));
  if (filters.q) p.set("q", filters.q);
  p.set("sort", sort.key);
  p.set("desc", sort.dir === "desc" ? "true" : "false");
  p.set("limit", String(limit));
  return p;
}

// ---- chips -------------------------------------------------------------------
// One chip per ACTIVE filter, each removable. The key reads muted and the value
// reads in the accent, so a row of chips scans as "Status · 2  Scope Development".
export type TicketFilterField = "status" | "scope" | "tag" | "effort";

export const FILTER_FIELDS: { field: TicketFilterField; label: string }[] = [
  { field: "status", label: "Status" },
  { field: "scope", label: "Scope" },
  { field: "tag", label: "Tag" },
  { field: "effort", label: "Effort" },
];

export type FilterChipSpec = {
  field: TicketFilterField;
  label: string;
  value: string;
};

/**
 * The chips to render, in a fixed order so they do not reshuffle as filters
 * change. `labelFor` renders a scope key (the page has the scope vocabulary,
 * this module does not) and `statusLabel` a status.
 */
export function chipsFor(
  filters: TicketFilters,
  view: TicketView,
  fmt: { scope: (key: string) => string; status: (s: string) => string },
): FilterChipSpec[] {
  const chips: FilterChipSpec[] = [];
  const many = (n: number) => `· ${n}`;

  // The status chip is present even at its default, and says which default it
  // is. "Where are the done tickets" is the most common question a filtered
  // list gets asked, and a control that only appears once you have already
  // changed it cannot answer that.
  if (isDefaultStatus(filters, view)) {
    chips.push({ field: "status", label: "Status", value: view === "board" ? "On the board" : "Active" });
  } else if (filters.status.length === 1) {
    chips.push({ field: "status", label: "Status", value: fmt.status(filters.status[0]) });
  } else if (filters.status.length > 1) {
    chips.push({ field: "status", label: "Status", value: many(filters.status.length) });
  }

  if (filters.scope) {
    chips.push({ field: "scope", label: "Scope", value: fmt.scope(filters.scope) });
  }
  if (filters.labels.length === 1) {
    chips.push({ field: "tag", label: "Tag", value: filters.labels[0] });
  } else if (filters.labels.length > 1) {
    chips.push({ field: "tag", label: "Tag", value: many(filters.labels.length) });
  }
  if (filters.effort.length === 1) {
    chips.push({ field: "effort", label: "Effort", value: filters.effort[0] });
  } else if (filters.effort.length > 1) {
    chips.push({ field: "effort", label: "Effort", value: many(filters.effort.length) });
  }
  return chips;
}

/** Clearing a chip. Status clears to EVERY status, not back to the default:
 *  the default is what the chip already said, so clearing to it would look
 *  like the control did nothing. */
export function clearField(f: TicketFilters, field: TicketFilterField): TicketFilters {
  switch (field) {
    case "status": return { ...f, status: [] };
    case "scope": return { ...f, scope: null };
    case "tag": return { ...f, labels: [] };
    case "effort": return { ...f, effort: [] };
  }
}

/** Toggle one value of a multi-value filter. */
export function toggleValue(
  f: TicketFilters, field: "status" | "tag" | "effort", value: string,
): TicketFilters {
  const key = field === "status" ? "status" : field === "tag" ? "labels" : "effort";
  const cur = f[key] as string[];
  const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
  return { ...f, [key]: next } as TicketFilters;
}

// ---- board grouping ----------------------------------------------------------
// Every fetched row lands in exactly one lane, and a row whose status is not a
// lane (parked, cancelled, or a status this build has not heard of) is NOT
// dropped: it goes to the end under its own heading. A board that silently
// loses work is worse than one with an extra column.
export type BoardLane = { status: string; items: { status?: string }[] };

export function boardLanes<T extends { status?: string }>(
  tasks: T[], lanes: string[] = BOARD_STATUSES,
): { status: string; items: T[] }[] {
  const byStatus = new Map<string, T[]>();
  for (const t of tasks) {
    const k = t.status ?? "";
    const arr = byStatus.get(k);
    if (arr) arr.push(t);
    else byStatus.set(k, [t]);
  }
  const out = lanes.map((status) => ({ status, items: byStatus.get(status) ?? [] }));
  for (const [status, items] of byStatus) {
    if (!lanes.includes(status)) out.push({ status, items });
  }
  return out;
}

// The mobile list groups by the same rule, minus the empty lanes: a phone
// scrolls, so an empty heading is pure cost there, while on a board an empty
// column is the drop target that says the lane exists.
export function listGroups<T extends { status?: string }>(
  tasks: T[], lanes: string[] = BOARD_STATUSES,
): { status: string; items: T[] }[] {
  return boardLanes(tasks, lanes).filter((g) => g.items.length > 0);
}
