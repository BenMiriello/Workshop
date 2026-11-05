import { reconnectDelayMs } from "./streamHealth";

// THE RECONNECT POLICY FOR A TURN'S OWN STREAM.
//
// It was a fixed 1200ms wait, eight tries, about ten seconds in all. A backend
// restart takes longer than that as often as not, so the tab gave up on a turn
// that was still running and fell back to a 5s poll that only re-attaches while
// the store still believes in the run. The schedule is now the change stream's
// (streamHealth.reconnectDelayMs: 1s floor, doubling, 30s ceiling, jittered so
// every tab dropped by one restart does not come back in the same instant), and
// the budget is a length of OUTAGE rather than a count of tries, because a count
// of exponentially growing waits says nothing about how long anyone waited.
//
// The clock starts at the first drop of a run of drops and stops only when a
// frame arrives. Re-attaching does not stop it: a socket that opens and dies
// before saying anything has proved nothing.

/** How long an outage is ridden out before the liveness poll takes over. */
export const TURN_RECONNECT_WINDOW_MS = 15 * 60_000;

export type TurnReconnect = {
  /** Consecutive drops since the last frame. */
  attempt: number;
  /** When the current run of drops began; null while frames are arriving. */
  since: number | null;
};

export const TURN_STREAM_HEALTHY: TurnReconnect = { attempt: 0, since: null };

/** The socket dropped. Returns the next state and how long to wait before
 *  re-attaching, or null once the outage has outlasted the window.
 *
 *  `rand` in [0,1) is the caller's, so the schedule runs in a test. */
export function turnStreamDropped(
  h: TurnReconnect,
  now: number,
  rand: number,
): { next: TurnReconnect; reattachAfterMs: number | null } {
  const since = h.since ?? now;
  if (now - since >= TURN_RECONNECT_WINDOW_MS) {
    return { next: { attempt: h.attempt, since }, reattachAfterMs: null };
  }
  const attempt = h.attempt + 1;
  return { next: { attempt, since }, reattachAfterMs: reconnectDelayMs(attempt, rand) };
}

/** What a turn's settle does once its syncs are done.
 *
 *  `settled`: a sync that SUCCEEDED no longer shows the run (a failed sync
 *  leaves the store as it was, so it can never set this). `ended`: the stream
 *  delivered `end`, which the server sends only when the run is over. Anything
 *  short of one of those two is not evidence the run finished, however many
 *  syncs failed, so the tab keeps following it. */
export function afterSettle(s: {
  settled: boolean;
  ended: boolean;
  stopped: boolean;
}): "retire" | "leave-to-stop" | "keep-following" {
  if (s.settled || s.ended) return "retire";
  return s.stopped ? "leave-to-stop" : "keep-following";
}
