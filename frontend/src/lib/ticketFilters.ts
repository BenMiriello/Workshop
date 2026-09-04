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
  TASK_SOURCES,
  TASK_STATUSES,
  type TaskSort,
  type TaskSource,
  type TaskStatus,
} from "./tasks";
import {
  ACTIVE_STATE_TYPES, STATUS_FILTERS, statusFilterStatuses, type StatusFilter,
} from "./ticketGroups";

// ---- views -------------------------------------------------------------------
// Two views, and they are the two that exist. A `?view=timeline` URL still
// falls back to the default rather than rendering a blank page, which is what
// this parse has always done for any unknown value; the difference is that the
// switcher no longer offers a Timeline nobody can open.
//
// `list`, NOT `table`. The table was the third shape of a ticket row on this
// one surface (a board card, a table row, a phone row), and it was the one
// shape the design system had already ruled out: `.ws-listrow` is "the one row
// shape for every list in the app: tickets and runs today", settled on operator
// direction 2026-08-12, and the table put tickets back on the grid it replaced
// -- eight columns of which four (tags, priority, effort, assignee) are empty
// on most rows. `?view=table` still resolves, to this, by the same fallback
// that retired `?view=timeline`.
export type TicketView = "list" | "board";

export const TICKET_VIEWS: TicketView[] = ["list", "board"];

export const DEFAULT_VIEW: TicketView = "list";

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
// It depends on the view, and that is not a shortcut. A LIST answering "what is
// in flight" must not open with 108 done tickets (measured on the live store:
// 108 of 177). A BOARD whose lanes are the statuses must not open with two
// permanently empty ones, which is what the list's default would do to it.
//
// So each view opens on the set it can actually draw, and an EXPLICIT status
// filter is honoured by both. The difference is only ever the default.
//
// The board's default is the EMPTY set, which is how this module spells "every
// status" -- the board narrows to its own six lanes itself (see `BOARD_STATUSES`
// and `TicketBoard`), so restating them as a filter would be a second place the
// lane list is decided, and it made the board's default match no preset on the
// status bar. Every lane the board draws is still exactly the six.
export const ACTIVE_STATUSES: TaskStatus[] =
  ACTIVE_STATE_TYPES.flatMap((t) => STATUSES_BY_STATE_TYPE[t]);

export function defaultStatuses(view: TicketView): TaskStatus[] {
  return view === "board" ? [] : ACTIVE_STATUSES;
}

// ---- the default SOURCE set, and why this list opens narrowed ----------------
// Measured on the live store 2026-09-04: 215 live tickets, of which 140 came
// through the agent door and 75 through the operator's own two (38 `operator`,
// 37 `chat`). An unfiltered list is therefore two thirds work an agent filed
// for itself while it was doing something else, and the operator's report is
// that he opens this page and cannot find the things he asked for.
//
// The same argument the status default already makes. A list answering "what is
// in flight" must not open with 108 done tickets, so `defaultStatuses` narrows;
// a list answering "what do I have to do" must not open with 140 tickets nobody
// asked for, so this narrows too. Both are DEFAULTS, both are spelled by
// leaving the URL param off, and both have a control on screen saying which one
// is on with the count behind every alternative. One tap widens it.
//
// IT IS STILL PROVENANCE, NOT INTENT. `operator` and `chat` are the two doors
// the operator's own words arrive through, which is the same pair the store
// already collapses (`tasks._SCOPED_BY_FROM_SOURCE` maps both to `operator`).
// It does not claim he wrote every one of them; it claims they came from him or
// from a conversation with him, which is what the field records. See
// lib/tasks TaskSource.
export const DEFAULT_SOURCES: TaskSource[] = ["operator", "chat"];

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
  /** `operator` or `agent:<slug>`. Open set: agents are registered at runtime
   *  and a deployment has as many people as it has. */
  assignee: string[];
  /** WHERE THE TICKET CAME FROM, from `TASK_SOURCES`. A row matches if it
   *  carries ANY of these.
   *
   *  Every ticket has carried this since the store existed and no view read it,
   *  while 132 of 208 open tickets were filed by an agent noticing something. It
   *  is a plain filter over a stored value: no inference, and it says where a
   *  ticket came from rather than what anyone intended. See lib/tasks
   *  TaskSource, including why `unknown` is one of the values. */
  source: string[];
};

export type TicketSort = { key: TaskSort; dir: "asc" | "desc" };

export const DEFAULT_SORT: TicketSort = { key: "updated", dir: "desc" };

/** The filter set a view opens on, before the URL says otherwise. */
export function defaultFilters(view: TicketView): TicketFilters {
  return {
    q: "", status: defaultStatuses(view), scope: null,
    labels: [], effort: [], assignee: [], source: [...DEFAULT_SOURCES],
  };
}

// Two sets, same members, order-independent. Used to decide whether a status
// filter is still the default (and so whether its chip reads "Active" or lists
// what was picked).
const sameSet = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && [...a].sort().join() === [...b].sort().join();

export const isDefaultStatus = (f: TicketFilters, view: TicketView): boolean =>
  sameSet(f.status, defaultStatuses(view));

export const isDefaultSource = (f: TicketFilters): boolean =>
  sameSet(f.source, DEFAULT_SOURCES);

// ---- status presets: the one control that is always on screen ---------------
// WHY THIS IS NOT A CHIP. Status was reachable only by opening a "+ Filter"
// menu (wide) or a filter sheet (phone) and ticking boxes, which is the right
// shape for tag and effort and the wrong one for the question a queue of ~200
// tickets is actually asked: what is blocked, what is left, what is done. A
// control you have to open cannot answer that, and its counts cannot be read at
// all. These five presets sit above the list at every width, each with the
// server's own count beside it, so "2 blocked" is one glance and one tap.
//
// The vocabulary is NOT new. `lib/ticketGroups.STATUS_FILTERS` already owns
// these five and the measurement behind them (five statuses are in use on the
// live store; ten of the fifteen options the old select offered could only ever
// return nothing). This maps them onto the multi-value `status` this module
// carries, so there is one status vocabulary and not a sixth.
export type TicketStatusPreset = {
  value: StatusFilter;
  label: string;
  /** Empty means every status, the same spelling the filter set uses. */
  statuses: TaskStatus[];
};

export const STATUS_PRESETS: TicketStatusPreset[] = STATUS_FILTERS.map((f) => ({
  value: f.value,
  label: f.label,
  statuses: statusFilterStatuses(f.value) ?? [],
}));

/** Which preset a status set IS, or null when it is a hand-picked combination.
 *  Null is a real state and not a fallback: a `?status=doing,in_review` link
 *  still resolves, the bar shows nothing pressed, and the status chip comes
 *  back so the reader has something to clear. */
export function statusPresetOf(status: TaskStatus[]): StatusFilter | null {
  return STATUS_PRESETS.find((p) => sameSet(p.statuses, status))?.value ?? null;
}

export function statusesForPreset(value: StatusFilter): TaskStatus[] {
  return STATUS_PRESETS.find((p) => p.value === value)?.statuses ?? [];
}

// ---- source presets: the other control that is always on screen -------------
// WHERE A TICKET CAME FROM, as three pills with the server's own counts, above
// the status bar and above the list, at every width.
//
// It is the same argument as the status bar one step earlier. Source was on
// every ticket since the store existed and was readable only by opening the
// "+ Filter" menu (wide) or the sheet (phone) and ticking two of five boxes, so
// "how much of this did I ask for" took three interactions and its answer was
// never a number. It is now the first thing on the page, because on a queue
// that is two thirds agent-filed it narrows harder than anything else here.
//
// THREE PRESETS, NOT FIVE KINDS, for the same reason the status bar carries
// five presets rather than eight statuses: the bar answers the question the
// page is opened with, and the question is "mine or theirs". The five kinds
// stay reachable as a URL (`?source=chat`), which renders no pressed pill and
// gets a removable chip instead, exactly like a hand-picked status set.
//
// `agents` covers `autogen` as well as `agent`: both are a machine filing work
// it decided on, which is the distinction being drawn. `unknown` is in neither
// group and only in `anywhere` -- a record nobody wrote a source for must not
// be counted as anyone's, which is the whole reason that value exists.
export type SourceFilter = "you" | "agents" | "anywhere";

export type TicketSourcePreset = {
  value: SourceFilter;
  label: string;
  /** Empty means every source, the same spelling the filter set uses. */
  sources: TaskSource[];
};

// "From anywhere" rather than "All", because the status bar sits directly under
// this one and already has an All. Two pills a row apart reading the same word
// about two different axes is how a control stops being read at all.
export const SOURCE_PRESETS: TicketSourcePreset[] = [
  { value: "you", label: "From you", sources: [...DEFAULT_SOURCES] },
  { value: "agents", label: "From agents", sources: ["agent", "autogen"] },
  { value: "anywhere", label: "From anywhere", sources: [] },
];

/** Which preset a source set IS, or null when it is a hand-picked combination. */
export function sourcePresetOf(source: string[]): SourceFilter | null {
  return SOURCE_PRESETS.find((p) => sameSet(p.sources, source))?.value ?? null;
}

export function sourcesForPreset(value: SourceFilter): TaskSource[] {
  return SOURCE_PRESETS.find((p) => p.value === value)?.sources ?? [];
}

/** Is anything narrowing the list beyond the view's own default? */
/** Is anything narrowing the list beyond the view's own default?
 *
 *  The DEFAULT source set does not count, the same way the default status set
 *  does not. It is what the page opens on, so counting it would make every
 *  fresh page load read as filtered and give the empty state a Clear button
 *  that clears back to the state it is already in. What an empty result under
 *  the default source needs is a different sentence and a different action, and
 *  `TicketsView` draws that case separately. */
export function isNarrowed(f: TicketFilters, view: TicketView): boolean {
  return !!f.q || !!f.scope || f.labels.length > 0 || f.effort.length > 0
    || f.assignee.length > 0 || !isDefaultSource(f) || !isDefaultStatus(f, view);
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
    key: (["updated", "created", "priority", "title", "status", "queue",
           "source"] as const)
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

  // Source is spelled exactly like status, and for the same reason: it has a
  // non-empty default, so "every source" needs a word of its own and cannot be
  // an absent param. An unrecognised kind is dropped; a param that is entirely
  // unrecognised falls back to the default rather than emptying the list.
  const rawSource = params.get("source");
  let source: TaskSource[];
  if (rawSource === ALL) source = [];
  else if (rawSource) {
    const known = csv(rawSource).filter((s) =>
      (TASK_SOURCES as string[]).includes(s)) as TaskSource[];
    source = known.length ? known : [...DEFAULT_SOURCES];
  } else source = [...DEFAULT_SOURCES];

  return {
    q: (params.get("q") ?? "").trim(),
    status,
    scope: params.get("scope") || null,
    labels: csv(params.get("tag")),
    effort: csv(params.get("effort")),
    assignee: csv(params.get("assignee")),
    source,
  };
}

// ---- paging ------------------------------------------------------------------
// A PAGE, not a growing limit. The list used to raise its own `limit` and
// re-fetch, which means a big queue eventually asks the server for every row it
// has and puts all of them in one DOM. That is fine at 188 tickets and wrong at
// any number this is actually built for.
export const PAGE_SIZE = 50;

/** 1-based, because it is in the URL and `?page=1` is the first page. */
export function parsePage(params: URLSearchParams): number {
  const n = Number(params.get("page"));
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1;
}

export const pageCount = (total: number, size = PAGE_SIZE): number =>
  Math.max(1, Math.ceil(total / Math.max(1, size)));

/** The page a given one clamps to once `total` is known. A filter that shrinks
 *  the set must not strand the reader on an empty page 9. */
export const clampPage = (page: number, total: number, size = PAGE_SIZE): number =>
  Math.min(Math.max(1, page), pageCount(total, size));

/** The next URL for a filter/sort/view change, dropping anything at its default. */
export function writeParams(
  filters: TicketFilters, sort: TicketSort, view: TicketView,
  base?: URLSearchParams, page = 1,
): URLSearchParams {
  // Anything this module does not own (a deep link's own params) survives.
  const p = new URLSearchParams(base ?? undefined);
  const set = (k: string, v: string | null) => (v ? p.set(k, v) : p.delete(k));

  set("view", view === DEFAULT_VIEW ? null : view);
  set("q", filters.q.trim() || null);
  set("scope", filters.scope);
  set("tag", filters.labels.join(",") || null);
  set("effort", filters.effort.join(",") || null);
  set("assignee", filters.assignee.join(",") || null);
  set("source", isDefaultSource(filters)
    ? null
    : (filters.source.length === 0 ? ALL : filters.source.join(",")));
  set("status", isDefaultStatus(filters, view)
    ? null
    : (filters.status.length === 0 ? ALL : filters.status.join(",")));
  set("sort", sort.key === DEFAULT_SORT.key ? null : sort.key);
  set("dir", sort.dir === DEFAULT_SORT.dir ? null : sort.dir);
  // Page 1 is the default, so it is spelled by leaving the param off.
  set("page", page > 1 ? String(page) : null);
  return p;
}

// ---- the API query -----------------------------------------------------------
// Every filter here pushes DOWN to GET /api/tasks, which is what keeps `total`
// and paging honest: a filter narrowed in the browser reports the count of the
// unnarrowed set and pages against it, so the number beside the title
// contradicts the rows beneath it.
export function apiQuery(
  filters: TicketFilters, sort: TicketSort, limit: number, offset = 0,
): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.status.length) p.set("status", filters.status.join(","));
  if (filters.scope) p.set("scope", filters.scope);
  if (filters.labels.length) p.set("labels", filters.labels.join(","));
  if (filters.effort.length) p.set("effort", filters.effort.join(","));
  if (filters.assignee.length) p.set("assignee", filters.assignee.join(","));
  if (filters.source.length) p.set("source", filters.source.join(","));
  if (filters.q) p.set("q", filters.q);
  p.set("sort", sort.key);
  p.set("desc", sort.dir === "desc" ? "true" : "false");
  p.set("limit", String(limit));
  if (offset > 0) p.set("offset", String(offset));
  return p;
}

// ---- the board asks per LANE -------------------------------------------------
// One query per column, not one query for the board.
//
// A single query cannot fill a board. The server sorts and pages the whole set,
// so `limit: 300` on a queue of 5,000 can legitimately come back as 300 done
// tickets and nothing else: the lanes that matter render empty while the data
// says they are full. Asking per lane makes each column its own paged list,
// which is what a column IS, and the lane's `total` is the honest count for its
// heading whatever is being displayed under it.
export const LANE_SIZE = 25;

export function laneQuery(
  filters: TicketFilters, sort: TicketSort, status: string, limit: number,
): URLSearchParams {
  // The lane IS the status filter. Everything else narrows it as usual.
  return apiQuery({ ...filters, status: [status as TaskStatus] }, sort, limit);
}

// ---- chips -------------------------------------------------------------------
// One chip per ACTIVE filter, each removable. The key reads muted and the value
// reads in the accent, so a row of chips scans as "Status · 2  Scope Development".
export type TicketFilterField =
  "status" | "scope" | "tag" | "effort" | "assignee" | "source";

export const FILTER_FIELDS: { field: TicketFilterField; label: string }[] = [
  { field: "status", label: "Status" },
  { field: "source", label: "Source" },
  { field: "scope", label: "Scope" },
  { field: "tag", label: "Tag" },
  { field: "effort", label: "Effort" },
  { field: "assignee", label: "Assignee" },
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
 *
 * `opts.lockedScope` says the surface IS one scope, so the scope is not a
 * filter on it: no chip, because a chip carries a clear button and clearing
 * this one would ask for a list the surface cannot show.
 *
 * It no longer takes the VIEW. It took one only to decide what the always-on
 * status chip should read at each view's default ("Active" or "On the board"),
 * and there is no always-on status chip now: the status bar is the control, and
 * a chip appears only for a hand-picked set, which reads the same in both.
 */
export function chipsFor(
  filters: TicketFilters,
  fmt: {
    scope: (key: string) => string;
    status: (s: string) => string;
    source: (s: string) => string;
  },
  opts?: { lockedScope?: boolean },
): FilterChipSpec[] {
  const chips: FilterChipSpec[] = [];
  const many = (n: number) => `· ${n}`;

  // STATUS TAKES A CHIP ONLY WHEN THE BAR CANNOT SAY IT. The status bar is on
  // screen at both widths with the five presets and their counts, so a chip
  // repeating "Status · Active" beside a pressed Active pill is the same fact
  // twice -- two chip-shaped objects per control is what stops any of them
  // being scannable (operator, 2026-08-12).
  //
  // A hand-picked set matches no preset, so nothing on the bar is pressed and
  // there would be no way back: that case keeps its chip, and the chip is how
  // it is cleared.
  if (statusPresetOf(filters.status) === null) {
    chips.push({
      field: "status",
      label: "Status",
      value: filters.status.length === 1
        ? fmt.status(filters.status[0])
        : many(filters.status.length),
    });
  }

  // SOURCE TAKES A CHIP ONLY WHEN THE BAR CANNOT SAY IT, the same rule as
  // status one paragraph up. `TicketSourceBar` is on screen at both widths with
  // its three presets and their counts, so a chip reading "Source · 2" beside a
  // pressed "From you" is the same fact twice. A hand-picked set (`?source=chat`)
  // presses no pill, so it keeps its chip and the chip is how it is cleared.
  if (sourcePresetOf(filters.source) === null) {
    chips.push({
      field: "source",
      label: "Source",
      value: filters.source.length === 1
        ? fmt.source(filters.source[0])
        : many(filters.source.length),
    });
  }

  if (filters.scope && !opts?.lockedScope) {
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
  if (filters.assignee.length === 1) {
    chips.push({ field: "assignee", label: "Assignee", value: filters.assignee[0] });
  } else if (filters.assignee.length > 1) {
    chips.push({ field: "assignee", label: "Assignee", value: many(filters.assignee.length) });
  }
  return chips;
}

/** Clearing a chip. Status and source clear to EVERY value, not back to their
 *  defaults: a chip only exists for a set that matches no preset, and clearing
 *  it to a default that has its own pressed pill would read as the control
 *  doing nothing. */
export function clearField(f: TicketFilters, field: TicketFilterField): TicketFilters {
  switch (field) {
    case "status": return { ...f, status: [] };
    case "scope": return { ...f, scope: null };
    case "tag": return { ...f, labels: [] };
    case "effort": return { ...f, effort: [] };
    case "assignee": return { ...f, assignee: [] };
    case "source": return { ...f, source: [] };
  }
}

/** Toggle one value of a multi-value filter. */
export function toggleValue(
  f: TicketFilters,
  field: "status" | "tag" | "effort" | "assignee" | "source",
  value: string,
): TicketFilters {
  const key = field === "status" ? "status"
    : field === "tag" ? "labels"
    : field === "effort" ? "effort"
    : field === "source" ? "source" : "assignee";
  const cur = f[key] as string[];
  const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
  return { ...f, [key]: next } as TicketFilters;
}

// ---- grouping -----------------------------------------------------------
// `boardLanes` and `listGroups` used to live here, and both are gone.
//
// The board never called `boardLanes`: it fetches one paged query PER LANE, so
// it never has a mixed array to divide. `listGroups` grouped the phone list by
// status -- honestly-looking headings ("Blocked 2") computed over the CURRENT
// PAGE of 50, so on ~200 tickets sorted by date the same heading reappeared on
// page after page carrying a different number each time, and none of them was
// the count of anything. The status bar answers that question with the
// server's own totals instead, at every width.

// ---- the numbered pager ------------------------------------------------------
// Which page numbers to draw. A pager for 2 pages and a pager for 400 are the
// same control, so the windowing is a rule rather than a special case per size:
//
//   1 2 [3] 4 5            (small: every page)
//   1 … 6 7 [8] 9 10 … 42  (large: the ends, and a window around here)
//
// `gap` is a skipped RANGE, never a skipped single page: replacing one number
// with an ellipsis costs the same width and takes away a destination, so a gap
// is only drawn where it hides two or more.
export type PageItem = number | "gap";

export function pageItems(current: number, pages: number, span = 2): PageItem[] {
  if (pages <= 1) return [1];
  const here = Math.min(Math.max(1, current), pages);
  const wanted = new Set<number>([1, pages]);
  for (let p = here - span; p <= here + span; p++) {
    if (p >= 1 && p <= pages) wanted.add(p);
  }
  const sorted = [...wanted].sort((a, b) => a - b);

  const out: PageItem[] = [];
  let prev = 0;
  for (const p of sorted) {
    // A hole of exactly one page is filled rather than elided.
    if (prev && p - prev === 2) out.push(prev + 1);
    else if (prev && p - prev > 2) out.push("gap");
    out.push(p);
    prev = p;
  }
  return out;
}
