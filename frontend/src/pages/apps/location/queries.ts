// EVERY QUERY KEY AND FETCHER FOR THE LOCATION APP, IN ONE PLACE.
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
// WHAT IT IS FOR. `/api/location/latest` has two readers today, the app page
// and the settings page, each fetching into its own `useState`; `/api/location
// /history` has THREE reads on the app page alone (the table's first page, the
// map's longer breadcrumb, and "load more"), which is three copies of one fact
// at three limits. Two readers share a cache entry, an in-flight request and
// an invalidation only if they spell the key identically, so the keys are
// built here, by a function per fact, and a misspelling becomes a missing
// export: a compile error, which is the only kind that stops this recurring.
//
// THE RULE: EVERY PARAMETER THAT APPEARS IN THE URL MUST APPEAR IN THE KEY.
// This app is the clearest case in the audit. The table asks for 50 rows at a
// growing offset and the map asks for a much longer track at offset 0, from
// the same route; with the limit and offset in closure state rather than in
// the key, the two reads are indistinguishable to the cache and a slow one
// lands on top of a newer one. In the key they are separate entries, resolved
// independently, and the stale one has nowhere to land.
//
// TYPES ARE GENERATED HERE, unlike the other apps' modules: `routes/location`
// declares `response_model=` on `/latest` and `/history`, so
// `api/schema.gen.ts` carries the real contract and these fetchers return it
// concretely. `frontend/CLAUDE.md` names this page as the case that motivated
// generating types at all -- its four hand-written record fields said `number
// | undefined` where the backend serialises `number | null`, on the one page
// whose whole job is reading them. Do not hand-write these back.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { shortHttpError } from "@workshop/ui";
import type { components } from "../../../api/schema.gen";

const API = "/api/location";

/** A key, once built. Readonly so a caller cannot mutate a shared tuple. */
export type LocationQueryKey = readonly unknown[];

/** One recorded position, straight off the backend's own model. */
export type LocationRecord = components["schemas"]["LocationRecord"];

/**
 * The history page. ADR 0023 envelope: `{"items": [...], "page": {"limit",
 * "next_cursor", "total", "offset"}}`, which is `Page[LocationRecord]` on the
 * backend and is why the generated name reads the way it does.
 */
export type LocationHistoryPage = components["schemas"]["Page_LocationRecord_"];

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return (await r.json()) as T;
}

export const locationKeys = {
  /** Everything under the app. The prefix an invalidation uses to reach it all. */
  all: () => ["location"] as const,

  /** `GET /api/location/latest` -- the most recent position, or null if none. */
  latest: () => ["location", "latest"] as const,

  /**
   * `GET /api/location/history`. BOTH paging params are in the key; see the
   * header for why this is the read that made the rule.
   */
  history: (p: { limit: number; offset: number }) =>
    ["location", "history", p.limit, p.offset] as const,

  /**
   * The prefix over every window of the history, for invalidating after the
   * clear-history write, which must drop the map's track and the table's pages
   * together.
   */
  historyAll: () => ["location", "history"] as const,
} as const;

// The fetchers. One per fact, taking the SAME params object as its key, so a
// call site cannot key a request by one set of params and send another.

/**
 * `null` is a real answer (no position recorded yet), not an error: the route
 * is declared `response_model=LocationRecord | None` and returns 200 with a
 * null body. A failed request throws, so a reader can tell "nothing yet" from
 * "could not load", which the current page cannot.
 *
 * THE ROUTE ANSWERS WITH THE RECORD BARE. There is no envelope: no `{latest:
 * ...}`, no `{item: ...}`. `backend/routes/location.py` declares
 * `response_model=LocationRecord | None` and returns the model itself, and
 * `backend/tests/test_ownership.py` asserts it by reading `json()["lat"]`.
 *
 * That sentence is here because a third reader got it wrong. The app launcher
 * fetched this URL itself and read `j.latest`, which is `undefined` against
 * this route for every possible response -- so its card said "no pings yet"
 * from the day it was written, permanently, while the app page one tap away
 * showed a live position. Nothing could catch it: the body was parsed as `any`,
 * both spellings type-check, and "no pings yet" is a perfectly plausible thing
 * for a launcher card to say. A shared typed fetcher is what makes that class
 * of disagreement unrepresentable, so EVERY reader of this route goes through
 * this function -- the app page, the settings panel, and the launcher probe in
 * `src/apps/registry.tsx`.
 */
export const fetchLatest = (): Promise<LocationRecord | null> =>
  jget<LocationRecord | null>("/latest");

export const fetchHistory = (p: { limit: number; offset: number }): Promise<LocationHistoryPage> =>
  jget<LocationHistoryPage>(`/history?limit=${p.limit}&offset=${p.offset}`);

/** `DELETE /api/location/history`. 204, no body: there is nothing to read back,
 *  only the reads above to invalidate. */
export async function clearHistory(): Promise<void> {
  const r = await fetch(`${API}/history`, { method: "DELETE" });
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

// ---- The reads themselves --------------------------------------------------
//
// A KEY AND A FETCHER ARE ONLY TWO THIRDS OF A SHARED READ. The third is the
// CADENCE, and it belongs here for the same reason the key does: two readers
// that spell the interval differently are two polls again, on one cache entry
// this time, which is better but still not one read. So `/latest` and the
// clear-history write are exported as hooks, not as raw options a call site
// re-types. The other reads on this app (the table's window and the map's
// track) have ONE reader each and stay at their call site, where the reason
// they are two separate reads is written down.

/** `/latest` polls at 30s, which is the cadence both readers already used. */
const LATEST_POLL_MS = 30_000;

/**
 * The most recent position. `null` means none has ever been recorded; a
 * FAILED read is `isError` with `data` untouched, so a caller can say the
 * right one of those two things (they used to be the same blank).
 *
 * ONE READ FOR THE WHOLE SCREEN. The app page polled this, and the settings
 * panel that renders INSIDE it (`AppSettings` mounts its children only while
 * the panel is open) polled it again on its own timer into its own `useState`.
 * Two timers on one fact meant the two halves of one screen could show
 * positions up to a full interval apart. On one key they cannot: whichever
 * tick fires writes the single entry BOTH readers render, and two ticks that
 * overlap collapse into one request. (With the panel open there are still two
 * interval timers, one per mounted reader. What they can no longer do is hold
 * two different answers, which was the defect.)
 */
export function useLatestLocation() {
  return useQuery({
    queryKey: locationKeys.latest(),
    queryFn: fetchLatest,
    // `usePoll`'s default was to PAUSE while the document is hidden and catch
    // up on the way back. `refetchIntervalInBackground` defaults to false,
    // which is the pause; the catch-up read is the client's global focus +
    // visibility listeners (`lib/queryClient.ts`). Neither half is restated
    // per site.
    refetchInterval: LATEST_POLL_MS,
    // This read owns its own cadence, so a staleness window over it would only
    // swallow that catch-up read.
    staleTime: 0,
    // `usePoll` DOES NOT RETRY: a failed tick is followed by the next tick.
    // The app's transient-retry default would put extra requests in front of a
    // poll that already re-reads 30 seconds later.
    retry: false,
  });
}

/**
 * Erase every stored position. One writer for both call sites (the app page's
 * "Clear all" and the settings panel's), so the invalidation cannot differ
 * between them: the write drops the latest position, the table's window AND
 * the map's track together, which is what `all()` reaches.
 *
 * The invalidation is RETURNED from `onSuccess`, so the mutation stays pending
 * until the re-reads land. The confirm dialog's busy state then covers the
 * whole operation rather than clearing while the page still shows the data it
 * just deleted.
 */
export function useClearLocationHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: clearHistory,
    onSuccess: () => qc.invalidateQueries({ queryKey: locationKeys.all() }),
  });
}
