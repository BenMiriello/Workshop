// One QueryClient for the app, and one set of defaults chosen against THIS
// app rather than against the library's own.
//
// Every read migrated onto Query was a hand-rolled `useState` + `useEffect` or
// a `usePoll` before it (docs/platform-redesign/client-state-implementation-2026-08-26.md
// section 4). The defaults below are picked so that a read that moves here
// behaves the way it already behaved. A default that refetches MORE than the
// code it replaces is not a migration, it is a new poll.
import { QueryClient } from "@tanstack/react-query";
import { isTransientNetworkError } from "@workshop/ui";

// A restart window is a pause, not a break. The kit's `withTransientRetry`
// rides one out with three waits before it gives up, so a query gives up after
// the same three. WHAT is worth retrying is not decided here: that judgement
// lives in `packages/ui/src/httpError.ts` and is imported, so a query and a
// hand-rolled poll cannot drift into disagreeing about it.
const TRANSIENT_RETRIES = 3;

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // The app polls on a 5-60s cadence today. An aggressive default (the
        // library's own is 0) would re-read more than the code it replaces,
        // which is the opposite of the point.
        staleTime: 30_000,
        // This is what `usePoll` ALREADY does: it listens on `visibilitychange`
        // and on `focus` and runs a catch-up read on the way back
        // (packages/ui/src/usePoll.ts). Leaving it on preserves that behaviour
        // instead of introducing new behaviour; a read that never had it turns
        // it off at the call site.
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        // Long enough that leaving a thread and coming back is a cache hit,
        // short enough that a chat left open for an afternoon is not holding
        // every payload it ever read.
        gcTime: 5 * 60_000,
        // A 502/503/504 from the edge while the backend comes back up, and a
        // fetch that never got a response at all, are worth waiting out. A 4xx
        // or a 500 is a real answer the server chose to give and must not be
        // retried away.
        retry: (failureCount, error) =>
          isTransientNetworkError(error) && failureCount < TRANSIENT_RETRIES,
      },
    },
  });
}

/** The app's client. One per document; the provider mounts it in `App.tsx`. */
export const queryClient = makeQueryClient();
