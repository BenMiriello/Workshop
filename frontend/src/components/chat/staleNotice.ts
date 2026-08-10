// "This chat has stopped updating", said once, plainly, and only when true.
//
// The failure it describes used to be invisible. A poll that failed reported
// itself ONLY when the transcript was empty, on the reasoning that an empty
// screen is the one that needs explaining. It is the opposite way round: an
// empty chat that fails to load is obviously broken, while a chat with a long
// conversation on screen looks completely normal while being minutes behind. A
// turn may have finished, a reply may be waiting, and this browser would not
// know. "The chat is stuck" has meant this more than once.
//
// It says how long, because that is the fact that decides what to do: fifteen
// seconds is a hiccup worth ignoring, four minutes means the backend is down and
// waiting will not help.

/** Round to a unit the reader can act on. Seconds below a minute, then whole
 *  minutes: a chat that is "3 minutes" behind and one that is "3.4 minutes"
 *  behind call for exactly the same thing. */
function agoText(ms: number): string {
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.round(secs / 60);
  return mins === 1 ? "1 minute" : `${mins} minutes`;
}

/** The line to show, or null when this browser is current.
 *
 *  `now` is passed in rather than read from the clock so the rule is executable
 *  in a test, which is the whole reason this is its own module. */
export function staleNoticeText(staleSince: number | null, now: number): string | null {
  if (staleSince === null) return null;
  // A clock that ran backwards (a sleeping laptop, an NTP step) must not produce
  // a notice that says the future.
  const ms = Math.max(0, now - staleSince);
  return `Not hearing from the server. This chat stopped updating ${agoText(ms)} ago.`;
}
