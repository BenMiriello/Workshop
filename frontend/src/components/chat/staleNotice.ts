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
//
// WHICH CLOCK THE FLOOR READS IS THE WHOLE RULE, and it was the wrong one.
// It measured from the last request that LANDED, which is not when the outage
// began: a quiet chat only asks every POLL_MS (60s), so a thread that had been
// idle for a minute was already a minute past its last success before anything
// went wrong. Two failures a second apart then satisfied a 30-second floor
// that nothing had been silent for, and the banner appeared roughly two
// seconds into a blip. It cleared itself the moment the next request landed,
// which is exactly what the operator reported on 2026-09-04: "why does this
// chat say 'This chat stopped updating' -- and then that went away."
//
// A backend restart makes those two quick failures easy to come by. The stream
// drops, its reconnect backoff syncs on the way back in, and the deferred
// restart window is a few seconds wide — so the condition the old rule tested
// (two failures, and the last success more than 30s ago) is satisfied by a
// restart the edge now waits out on its own.
//
// The floor measures the OUTAGE now: how long this browser has been failing
// CONTINUOUSLY, timed from the first failure of the current run. A success
// ends the run and there is nothing to time. That makes the sentence true when
// it appears, and it keeps the case the floor was added for: with a 60s poll,
// two consecutive failed polls are 60s apart, which is past the floor.

/** How long this browser must have been FAILING, continuously, before the
 *  silence is worth stating. Comfortably longer than any retry burst and than
 *  a backend restart (the edge waits ~7s out by itself, see
 *  workshop_public_app), and short enough that a real outage is named while
 *  the operator is still looking at the screen. */
export const STALE_AFTER_MS = 30_000;

/** How many failures in a row before elapsed time is even consulted. */
export const STALE_AFTER_FAILURES = 2;

/** The one sentence. No elapsed figure, deliberately: past the floor the answer
 *  is the same either way (the backend is not there, press Retry), and a
 *  duration rendered from a chat that has stopped re-rendering ages into a lie.
 *  That is the whole reason the old copy could say "3s" during a real outage. */
export const STALE_TEXT = "This chat stopped updating.";

/** Whether to say it.
 *
 *  `failures` is the current run of consecutive failures; `since` is when that
 *  run STARTED (the moment of its first failure). Both are reset by any
 *  request that lands, so together they describe one uninterrupted outage and
 *  nothing else. `since` is meaningless when `failures` is 0 and is not read.
 *
 *  `now` is passed in rather than read from the clock so the rule is executable
 *  in a test, which is the whole reason this is its own module. */
export function isStale(failures: number, since: number, now: number): boolean {
  if (failures < STALE_AFTER_FAILURES) return false;
  // A clock that ran backwards (a sleeping laptop, an NTP step) must not
  // manufacture silence that did not happen.
  return Math.max(0, now - since) >= STALE_AFTER_MS;
}
