// One QueryClient for the app, and one set of defaults chosen against THIS
// app rather than against the library's own.
//
// Every read migrated onto Query was a hand-rolled `useState` + `useEffect` or
// a `usePoll` before it (docs/platform-redesign/client-state-implementation-2026-08-26.md
// section 4). The defaults below are picked so that a read that moves here
// behaves the way it already behaved. A default that refetches MORE than the
// code it replaces is not a migration, it is a new poll.
import { QueryClient, focusManager } from "@tanstack/react-query";
import { isTransientNetworkError, TRANSIENT_BACKOFF_MS } from "@workshop/ui";

// Query's own focus detection listens on `visibilitychange` and NOTHING else
// (query-core/src/focusManager.ts). `usePoll` listens on both, and says why:
// "Focus too: a PWA restored from the app switcher does not always fire
// visibilitychange, and a poll that silently never resumes is worse than one
// that never paused." A read that moved off `usePoll` onto Query would have
// come back weaker than the code it replaced, so the app teaches the focus
// manager both events, once, here — beside the client, so the next read to
// migrate inherits it without anyone remembering to.
//
// The listener notifies with no argument on purpose, exactly as the library's
// own default does: passing a boolean goes through `setFocused`, which drops a
// repeat of the value it already holds, and the second of a visibilitychange
// and a focus for the same restore IS a repeat. Notifying unconditionally is
// what makes the catch-up read happen whichever event the platform sends.
function watchFocusAndVisibility(): void {
  focusManager.setEventListener((onFocus) => {
    if (typeof window === "undefined") return undefined;
    const wake = () => onFocus();
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  });
}

/** The app's client. One per document; the provider mounts it in `App.tsx`. */
export const queryClient = new QueryClient({
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
      //
      // BOTH halves come from the kit's one schedule. Neither WHAT is worth
      // retrying nor HOW LONG we wait is decided here: `TRANSIENT_BACKOFF_MS`
      // is the same array `withTransientRetry` rides a restart out on
      // (packages/ui/src/httpError.ts), so a query and a hand-rolled poll
      // cannot drift into disagreeing about either. A restated count here and
      // the library's exponential default is how they came to wait 7.0s for
      // the outage the kit waits 4.1s for.
      retry: (failureCount, error) =>
        isTransientNetworkError(error) && failureCount < TRANSIENT_BACKOFF_MS.length,
      // `failureCount` is 0-based, so this indexes the schedule directly. A
      // query that overrides `retry` with a larger count still gets a real
      // delay rather than `undefined`, which the retryer would sleep for 0ms.
      retryDelay: (failureCount) =>
        TRANSIENT_BACKOFF_MS[Math.min(failureCount, TRANSIENT_BACKOFF_MS.length - 1)],
    },
  },
});

watchFocusAndVisibility();
