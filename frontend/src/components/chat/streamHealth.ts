// THE RECONNECT POLICY FOR A CHANGE STREAM, as a rule instead of as three
// closures inside an effect.
//
// The chat holds an `EventSource` per open thread that says, with no content at
// all, "this thread moved" — and the client answers with the same cursor sync it
// would have made anyway. Keeping it up across a backend restart is the whole
// job, and the way it was written got three things wrong that a blip makes
// visible.
//
// 1. IT FOUGHT THE BROWSER'S OWN RETRY. `EventSource` reconnects by itself and
//    fires `error` while it is doing so (`readyState === CONNECTING`). The
//    handler closed the socket on every `error` regardless and scheduled its
//    own reopen, so a single drop produced two reconnect machines racing, and
//    the manual one climbed its backoff on errors the browser was already
//    handling. Only a socket the browser has GIVEN UP on (`CLOSED`) is this
//    code's problem. `useTurnStream` has always checked that; this stream did
//    not.
//
// 2. IT RESET THE BACKOFF ON TRAFFIC, NOT ON CONNECTING. The counter came down
//    only when a `changed` event arrived, so a stream that reconnected fine to a
//    quiet thread stayed at whatever attempt number the last outage left it at,
//    and the NEXT drop waited up to thirty seconds before trying. Proof that the
//    connection works is the connection opening, which is what `onOpen` is.
//
// 3. IT SYNCED ON THE WAY IN, NOT ON THE WAY BACK. Every scheduled retry fired
//    a `sync()` BEFORE reopening, i.e. while the backend was still away, so a
//    ten-second outage spent its whole length issuing requests that could only
//    fail. Those failures are what the staleness detector counts, which is one
//    half of how "This chat stopped updating." reached the screen two seconds
//    into a restart (see staleNotice). The catch-up belongs on `open`, where the
//    connection has just proved it works.
//
// Jitter is here for the ordinary reason and one specific to this app: a
// restart drops every stream in every open tab at the same instant, and a fixed
// schedule brings them all back in the same instant too.

/** First wait after a drop, and the floor under every jittered wait. */
export const RECONNECT_BASE_MS = 1_000;
/** The ceiling. Past this the 60s backstop poll is doing the real work anyway
 *  (useThreadLog POLL_MS), so waiting longer buys nothing. */
export const RECONNECT_MAX_MS = 30_000;

export type StreamPhase =
  /** A socket is open and has said so. */
  | "open"
  /** No socket of ours is open, and a reopen is scheduled. */
  | "waiting"
  /** A socket exists but has not opened yet — either the first one, or the
   *  browser's own retry, which we leave alone. */
  | "connecting";

export type StreamHealth = {
  phase: StreamPhase;
  /** Consecutive drops WE have had to reopen from. Zero while healthy. */
  attempt: number;
};

export const NEW_STREAM: StreamHealth = { phase: "connecting", attempt: 0 };

/** The socket opened. This, and nothing else, is proof the connection works, so
 *  it is where the backoff comes back to zero. */
export function streamOpened(): StreamHealth {
  return { phase: "open", attempt: 0 };
}

/** The socket errored.
 *
 *  `browserRetrying` is `readyState === CONNECTING`: the browser is already
 *  reopening this socket and will use its own schedule. Doing anything here
 *  would be a second socket behind the first.
 *
 *  Returns the next health and, when it is ours to reopen, how long to wait.
 *  `rand` (in [0,1)) is supplied by the caller so the schedule is executable in
 *  a test rather than only observable in a browser. */
export function streamErrored(
  h: StreamHealth,
  browserRetrying: boolean,
  rand: number,
): { next: StreamHealth; reopenAfterMs: number | null } {
  if (browserRetrying) return { next: { ...h, phase: "connecting" }, reopenAfterMs: null };
  const attempt = h.attempt + 1;
  return {
    next: { phase: "waiting", attempt },
    reopenAfterMs: reconnectDelayMs(attempt, rand),
  };
}

/** Capped exponential backoff with full jitter above the base.
 *
 *  The floor matters more than the ceiling here: a retry fired immediately
 *  after a failure lands inside the same outage and fails identically, which is
 *  the mistake the edge proxy had to be fixed for on 2026-09-04 (its single
 *  retry had no gap). So the wait is never below `RECONNECT_BASE_MS`; the
 *  jitter spends the room above it. */
export function reconnectDelayMs(attempt: number, rand: number): number {
  const n = Math.max(1, Math.floor(attempt));
  const ceiling = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** (n - 1));
  const spread = Math.max(0, ceiling - RECONNECT_BASE_MS);
  const r = Number.isFinite(rand) ? Math.min(0.999999, Math.max(0, rand)) : 0;
  return Math.round(RECONNECT_BASE_MS + spread * r);
}
