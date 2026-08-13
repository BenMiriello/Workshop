// "This chat stopped updating", said once, plainly, and only when true.
//
// The failure it describes used to be invisible. A poll that failed reported
// itself ONLY when the transcript was empty, on the reasoning that an empty
// screen is the one that needs explaining. It is the opposite way round: an
// empty chat that fails to load is obviously broken, while a chat with a long
// conversation on screen looks completely normal while being minutes behind. A
// turn may have finished, a reply may be waiting, and this browser would not
// know. "The chat is stuck" has meant this more than once.
//
// WHAT IT MUST NOT DO IS CRY WOLF. It shipped as a run of three failures with
// no clock on it, so a burst of three retries a second apart announced "this
// chat stopped updating 3s ago" — over the composer, in a chat that was working
// fine. Reported 2026-08-12. A three-second gap is not a fact worth a word,
// and a notice that fires on one teaches the reader to ignore the notice that
// matters.
//
// So the rule has two parts and needs BOTH: repeated failures (one is a
// hiccup) and real elapsed silence. The floor is what makes the sentence true
// when it appears.

/** How long this browser must have been out of contact before the silence is
 *  worth stating. Comfortably longer than any retry burst, and shorter than the
 *  60s backstop poll's own period, so the second consecutive failed poll is
 *  what surfaces a backend that has actually gone away. */
export const STALE_AFTER_MS = 30_000;

/** How many failures in a row before elapsed time is even consulted. */
export const STALE_AFTER_FAILURES = 2;

/** The one sentence. No elapsed figure, deliberately: past the floor the answer
 *  is the same either way (the backend is not there, press Retry), and a
 *  duration rendered from a chat that has stopped re-rendering ages into a lie.
 *  That is the whole reason the old copy could say "3s" during a real outage. */
export const STALE_TEXT = "This chat stopped updating.";

/** Whether to say it. `lastOkAt` is when a request last landed; on a browser
 *  that has never had one it is the moment the thread was opened, so the floor
 *  measures silence rather than uptime.
 *
 *  `now` is passed in rather than read from the clock so the rule is executable
 *  in a test, which is the whole reason this is its own module. */
export function isStale(failures: number, lastOkAt: number, now: number): boolean {
  if (failures < STALE_AFTER_FAILURES) return false;
  // A clock that ran backwards (a sleeping laptop, an NTP step) must not
  // manufacture silence that did not happen.
  return Math.max(0, now - lastOkAt) >= STALE_AFTER_MS;
}
