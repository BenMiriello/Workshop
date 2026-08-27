// EVERY QUERY KEY AND FETCHER FOR THE COMMUTE APP, IN ONE PLACE.
//
// WHY THIS FILE EXISTS, AND WHY IT IS NOT IN `lib/queryKeys.ts`.
// The platform's key registry is `frontend/src/lib/queryKeys.ts` and this file
// follows its pattern exactly: a nested object of factory functions returning
// `as const` tuples, commented where a slot's reason is not obvious. It cannot
// live there, because a platform source file may not name a specific app
// (CLAUDE.md, gated by `scripts/check_no_app_names.py`). The registry says so
// itself: "APPS DO NOT APPEAR HERE." So an app declares its own keys beside
// its own fetchers.
//
// The filename is load-bearing. `scripts/check_frontend_state.py` counts
// `queryKey: [` literals outside a file named `queryKeys.ts` or `queries.ts`
// and fails on a new one, so a key written at a call site is a build failure.
//
// WHAT IT IS FOR. `/api/commute/status` has two independent readers today --
// the page's brief header and `useCommuteNative.ts` -- each with its own poll
// and its own copy in `useState`. `sources` and `alerts/recent` each poll on
// their own 30s timer. Two readers share a cache entry, an in-flight request
// and an invalidation only if they spell the key identically, so the keys are
// built here, by a function per fact, and a misspelling becomes a missing
// export: a compile error, which is the only kind that stops this recurring.
//
// THE RULE: EVERY PARAMETER THAT APPEARS IN THE URL MUST APPEAR IN THE KEY.
// The messages list is the case that matters here. It pages by `offset`, and
// with the offset held in closure state instead of the key, a slow page-2
// response can land after page 3 and overwrite it. Params in the key make
// that unrepresentable: a different URL is a different cache entry, resolved
// independently, and the stale one has nowhere to land.
//
// TYPES ARE AT THE CALL SITES, and that is the backend's doing. `frontend/
// CLAUDE.md` says to prefer `api/schema.gen.ts` over a hand-written response
// type, but no route under `/api/commute` declares a `response_model=`, so
// every generated body is `Record<string, never>` -- the backend declining to
// say what it returns, not a gap in the generator. Rather than hand-write a
// duplicate contract, the fetchers are generic in the payload: a call site
// passes the shape it already declares. When a route gains a
// `response_model=`, swap that argument for `components["schemas"][...]` and
// delete the local type.
import { shortHttpError } from "@workshop/ui";

const API = "/api/commute";

/** A key, once built. Readonly so a caller cannot mutate a shared tuple. */
export type CommuteQueryKey = readonly unknown[];

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return (await r.json()) as T;
}

export const commuteKeys = {
  /** Everything under the app. The prefix an invalidation uses to reach it all. */
  all: () => ["commute"] as const,

  /** `GET /api/commute/sources` -- the feed registry plus each feed's health. */
  sources: () => ["commute", "sources"] as const,

  /**
   * `GET /api/commute/alerts/recent`. The limit is in the key: it is the only
   * thing that varies about this read, and a 50-row answer must not be served
   * to a caller that asked for fewer.
   */
  alertsRecent: (p: { limit: number }) => ["commute", "alerts", "recent", p.limit] as const,

  /**
   * `GET /api/commute/messages`, as ONE paged list rather than one entry per
   * page. The page size is in the key because it changes what a page IS; the
   * offset is the list's cursor, held by `useInfiniteQuery` as the page param
   * of this single entry.
   *
   * THAT IS THE POINT, and it is the fix for a measured bug. Keyed per offset,
   * "page 1" and "page 2" are two unrelated facts, and the feed's 30s poll
   * re-read page 1 and REPLACED the rendered list with it: load two more pages,
   * wait thirty seconds, watch the list collapse back to twelve rows. One entry
   * makes the loaded pages one thing, so a refetch re-reads all of them, in the
   * order they were fetched, into the slots they already occupy. An older
   * response still has nowhere to land -- not because its offset is in the key
   * now, but because the library resolves the pages sequentially into fixed
   * positions.
   */
  messagesPaged: (p: { limit: number }) => ["commute", "messages", "paged", p.limit] as const,
  /** The prefix over the list, for invalidating after a "check now" write. */
  messagesAll: () => ["commute", "messages"] as const,

  /** `GET /api/commute/status` -- the brief. Two readers, one key. */
  status: () => ["commute", "status"] as const,

  /** `GET /api/commute/schedule` -- the editable weekly plan. */
  schedule: () => ["commute", "schedule"] as const,
} as const;

// The fetchers. One per fact, taking the SAME params object as its key, so a
// call site cannot key a request by one set of params and send another.

export const fetchSources = <T>(): Promise<T> => jget<T>("/sources");

export const fetchAlertsRecent = <T>(p: { limit: number }): Promise<T> =>
  jget<T>(`/alerts/recent?limit=${p.limit}`);

export const fetchMessages = <T>(p: { limit: number; offset: number }): Promise<T> =>
  jget<T>(`/messages?limit=${p.limit}&offset=${p.offset}`);

export const fetchStatus = <T>(): Promise<T> => jget<T>("/status");

export const fetchSchedule = <T>(): Promise<T> => jget<T>("/schedule");

// ── The reads with more than one owner ───────────────────────────────────────
//
// A key alone is not enough to keep two readers on ONE cache entry: they must
// also agree on the fetcher and on the read policy, and `retry`/`staleTime`
// drifting apart between two `useQuery` calls is invisible until one of them
// starts behaving differently. So the shared reads are exported as options
// builders. A call site says what is different about ITS use (whether it is
// enabled, how often it ticks) and nothing else, which is the only part that
// legitimately differs.

/** How often a status reader ticks, and whether it keeps ticking while hidden. */
export type StatusReadOpts = {
  /** Skip the read entirely while false. Nothing is fetched and no timer runs. */
  enabled?: boolean;
  /** This reader's own cadence. The shared entry ticks at the SHORTEST cadence
   *  any live reader asks for, which is what makes two cadences safe. */
  refetchMs: number;
  /** `usePoll`'s `whenHidden: "continue"`. Only the reader that drives
   *  lock-screen features asks for this; a reader that only paints pixels has
   *  nothing to do while the app is hidden. */
  whileHidden?: boolean;
};

/**
 * `GET /api/commute/status` -- the brief. Two readers: the page's Status tab
 * and the native hook that drives the notification and the Live Activity.
 * Before this they were two polls of one URL at two cadences with two copies
 * in `useState`; now they are two observers of one entry.
 *
 * A WARNING FOR ANYONE HANGING A SIDE EFFECT OFF THIS DATA. `scoring.evaluate`
 * stamps every response with `ts`, so two reads of an unchanged commute are
 * never deeply equal: structural sharing cannot collapse them, `q.data` is a
 * new object every time, and `dataUpdatedAt` moves every time. NEITHER is a
 * change signal. `useCommuteNative` derives a value signature from the few
 * fields its notification and Live Activity actually depend on, and keys its
 * effect on that; read it before adding a second effect here.
 */
export function commuteStatusQuery<T>(o: StatusReadOpts) {
  return {
    queryKey: commuteKeys.status(),
    queryFn: () => fetchStatus<T>(),
    enabled: o.enabled ?? true,
    refetchInterval: o.refetchMs,
    refetchIntervalInBackground: o.whileHidden ?? false,
    // This read owns its own cadence, so a staleness window over it would only
    // swallow the catch-up read on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry: a failed tick is simply followed by the next
    // one. Extra attempts in front of a cadence that already re-reads are new
    // requests, not a migration.
    retry: false,
  };
}

/** One entry in the weekly plan. */
export type ScheduleEntry = {
  id: string;
  direction: "to-work" | "to-home";
  days: string[];
  // Anchor model: set the ONE end you care about; the other is derived from
  // leg durations by the backend (see scoring.resolve_times).
  anchor?: "arrive" | "depart";
  anchor_time?: string;
  buffer_minutes?: number;
  // Legacy two-field form -- still read so pre-anchor schedules still populate.
  target_depart_home?: string;
  target_arrive_work?: string;
  target_depart_work?: string;
  target_arrive_home?: string;
  flex_minutes_late?: number;
  flex_minutes_early?: number | null;
  legs_id?: string;
};

/** A place the commute starts or ends. */
export type SchedulePlace = { address?: string; lat?: number | null; lon?: number | null };

/**
 * `GET /api/commute/schedule`. DECLARED ONCE, beside the key, and that is a
 * deliberate exception to the header's "types are at the call sites": this
 * payload has two readers (the Schedule tab's editor and the app's settings
 * panel) and they share a cache entry. One entry holding two independently
 * hand-written views of its shape is the same defect as one endpoint under two
 * keys, one step further in.
 */
export type ScheduleDoc = {
  operator?: string;
  home?: SchedulePlace;
  work?: SchedulePlace;
  schedules: ScheduleEntry[];
  notes?: string;
};

/**
 * The schedule read. No `refetchInterval`: the weekly plan changes when
 * somebody saves it, and a save invalidates this key, so nobody has to poll
 * for it. It does NOT own a cadence either, so it keeps the app's transient
 * retry rather than turning retry off.
 */
export function commuteScheduleQuery() {
  return {
    queryKey: commuteKeys.schedule(),
    queryFn: () => fetchSchedule<ScheduleDoc>(),
  };
}
