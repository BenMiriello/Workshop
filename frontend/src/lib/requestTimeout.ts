// HOW LONG A REQUEST MAY WAIT FOR AN ANSWER BEFORE IT COUNTS AS A BLIP.
//
// Without a deadline a request to a stalled backend waits as long as the edge
// lets it (120s for an ordinary request), and every retry path in the app is
// keyed on the request FAILING. A read that hangs never fails, so the screen
// waiting on it sat on a skeleton or a pending bubble for the whole stall.
//
// The deadline covers the wait for the RESPONSE HEADERS only. Once they arrive
// the timer is cleared and the body reads at whatever pace it reads, so a large
// download or a slow body is never cut off. A server that answers at all
// answers its headers quickly; a server that sends nothing is the case this is
// for.
//
// A missed deadline rejects as a `TimeoutError`, the name `AbortSignal.timeout`
// uses, which the kit's `isTransientNetworkError` counts as transient. So every
// existing retry (the query client's, `withTransientRetry`, the send pipeline's)
// rides it out exactly like a 502.

/** The deadline for an ordinary read. */
export const READ_TIMEOUT_MS = 15_000;

/** The deadline for posting a chat message. Starting a turn assembles its
 *  prompt before it answers, so it is allowed longer than a read. */
export const SEND_TIMEOUT_MS = 20_000;

/** The error a missed deadline rejects with. */
export function requestTimeout(ms: number): Error {
  const message = `No answer from the server within ${Math.round(ms / 1000)}s.`;
  if (typeof DOMException === "function") return new DOMException(message, "TimeoutError");
  const e = new Error(message);
  e.name = "TimeoutError";
  return e;
}

export function isRequestTimeout(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "TimeoutError";
}

/** Which requests the global fetch wrapper gives a read deadline.
 *
 *  Reads only. A write may legitimately take as long as the work it starts
 *  (a compaction, a build, an upload), and repeating one is not always safe, so
 *  a write that wants a deadline sets its own (see `withDeadline`). A caller
 *  that passes its own `signal` owns cancellation, timeout included. Streams
 *  are `EventSource`, which never goes through `fetch`. */
export function readDeadlineFor(method: string | undefined, hasSignal: boolean): number | null {
  if (hasSignal) return null;
  const m = (method || "GET").toUpperCase();
  return m === "GET" || m === "HEAD" ? READ_TIMEOUT_MS : null;
}

/** Run a request with a deadline on its response headers. */
export async function withDeadline(
  run: (signal: AbortSignal) => Promise<Response>,
  ms: number,
): Promise<Response> {
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctl.abort();
  }, ms);
  try {
    return await run(ctl.signal);
  } catch (e) {
    if (timedOut) throw requestTimeout(ms);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
