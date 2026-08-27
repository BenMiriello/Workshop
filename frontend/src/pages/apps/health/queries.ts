// EVERY QUERY KEY AND FETCHER FOR THE HEALTH APP, IN ONE PLACE.
//
// WHY THIS FILE EXISTS, AND WHY IT IS NOT IN `lib/queryKeys.ts`.
// The platform's key registry is `frontend/src/lib/queryKeys.ts` and this file
// follows its pattern exactly: a nested object of factory functions returning
// `as const` tuples, commented where a slot's reason is not obvious. It cannot
// live there, because a platform source file may not name a specific app
// (CLAUDE.md, gated by `scripts/check_no_app_names.py`). The registry says so
// itself: "APPS DO NOT APPEAR HERE." So an app declares its own keys beside
// its own fetchers, the same way the location and commute apps do.
//
// The filename is load-bearing. `scripts/check_frontend_state.py` counts
// `queryKey: [` literals outside a file named `queryKeys.ts` or `queries.ts`
// and fails on a new one, so a key written at a call site is a build failure.
//
// THIS APP HAS ONE READER TODAY (the settings panel is its entire frontend),
// so this is a small file. It exists anyway because the read it describes is
// shared the moment anything else wants the latest metrics, and because the
// clear-history write has to invalidate that read from wherever it is called.
//
// TYPES ARE GENERATED: `routes/health` declares
// `response_model=dict[str, MetricLatest]` on `/latest`, so `api/schema.gen.ts`
// carries the real contract. The hand-written record this replaces said
// `ts?: string` where the backend serialises `string | null`. Do not hand-write
// it back (frontend/CLAUDE.md, "API response types are GENERATED").
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { shortHttpError } from "@workshop/ui";
import type { components } from "../../../api/schema.gen";

const API = "/api/health";

/** A key, once built. Readonly so a caller cannot mutate a shared tuple. */
export type HealthQueryKey = readonly unknown[];

/** One metric's most recent value, straight off the backend's own model. */
export type MetricLatest = components["schemas"]["MetricLatest"];

/** `/latest` is an index keyed by metric name, not a list. */
export type HealthLatest = Record<string, MetricLatest>;

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return (await r.json()) as T;
}

export const healthKeys = {
  /** Everything under the app. The prefix an invalidation uses to reach it all. */
  all: () => ["health"] as const,

  /** `GET /api/health/latest` -- the per-metric index of most recent values. */
  latest: () => ["health", "latest"] as const,
} as const;

/**
 * An EMPTY index is a real answer: the route returns `{}` until Health Auto
 * Export has pushed something. A failed request throws, so a reader can tell
 * "nothing pushed yet" from "could not load" -- which matters more here than
 * it looks, because this route requires a session and the panel used to render
 * an expired one as "no data, install Health Auto Export".
 */
export const fetchLatest = (): Promise<HealthLatest> => jget<HealthLatest>("/latest");

/** `DELETE /api/health/history`. 204, no body: nothing to read back, only the
 *  read above to invalidate. */
export async function clearHistory(): Promise<void> {
  const r = await fetch(`${API}/history`, { method: "DELETE" });
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

// ---- The reads themselves --------------------------------------------------
//
// The CADENCE lives here beside the key, for the same reason the key does: an
// interval restated at a call site is how one fact ends up polled at two
// speeds. 60s is what the panel already used.
const LATEST_POLL_MS = 60_000;

/** The per-metric latest values, polled. */
export function useHealthLatest() {
  return useQuery({
    queryKey: healthKeys.latest(),
    queryFn: fetchLatest,
    // `usePoll`'s default was to PAUSE while the document is hidden and catch
    // up on the way back. `refetchIntervalInBackground` defaults to false,
    // which is the pause; the catch-up read is the client's global focus +
    // visibility listeners (`lib/queryClient.ts`).
    refetchInterval: LATEST_POLL_MS,
    // This read owns its own cadence, so a staleness window over it would only
    // swallow that catch-up read.
    staleTime: 0,
    // `usePoll` DOES NOT RETRY: a failed tick is followed by the next tick.
    retry: false,
  });
}

/**
 * Erase every stored metric. The invalidation is RETURNED from `onSuccess`, so
 * the mutation stays pending until the re-read lands and the confirm dialog's
 * busy state covers the whole operation instead of clearing while the panel
 * still lists the metrics it just deleted.
 */
export function useClearHealthHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: clearHistory,
    onSuccess: () => qc.invalidateQueries({ queryKey: healthKeys.all() }),
  });
}
