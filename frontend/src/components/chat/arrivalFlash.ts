// WHETHER A MESSAGE IS NEWS RIGHT NOW, which is not the same question as
// whether it arrived after this list mounted.
//
// The transient half of LIVE (frontend/CLAUDE.md, "LIVE has two durations"):
// a message that lands while the reader is looking flashes and settles within
// a second, so it says "look here" and then stops saying it. The flash is a CSS
// animation carried by a class (`chat-msg--enter`, `ws-arrive`,
// `chat-bubble--confirm`), and a CSS animation runs whenever the element
// carrying the class is CREATED.
//
// THAT IS WHY THIS TAKES A CLOCK. The rule used to be `ts > mountedAt` and
// nothing else, which is a permanent property of a message: once true it was
// true for the rest of the session. So every message of a long conversation
// carried the arrival class indefinitely, and any moment that recreated those
// DOM nodes replayed the flash on all of them at once — the transcript
// re-animating end to end, for messages that had been sitting there for an
// hour. The operator reported it on 2026-09-04: "the chat will reset, and then
// all of the messages will reanimate and load in rapid succession, and it
// looks crazy."
//
// The list has several ways to be recreated and there is no value in policing
// them one at a time: a thread that goes blank and comes back, the drag
// overlay that replaces the log while a file is over the panel, a route
// remount, a reload. A one-shot signal that has already been spent is the fix
// that holds for all of them, because a flash that has already played cannot
// play again however many times its node is rebuilt.
//
// So a message flashes only inside a short window after it arrived. Past that,
// it is not news to this reader whatever happens to the DOM.

/** How long after a message's own timestamp the arrival flash is still news.
 *
 *  Generously longer than the animation itself (~1s) for two reasons that are
 *  both about clocks rather than taste: the server writes `ts` at seconds
 *  resolution (`chat_log.now_iso`), so a message can read as up to a second
 *  older than it is, and the browser's clock and the backend's need not agree
 *  to the second. Short enough that a batch of messages folded in after a
 *  reconnect, or a transcript rebuilt for any reason, is settled rather than
 *  strobing. */
export const ARRIVAL_FLASH_MS = 8_000;

/** Does something that happened at epoch-ms `at` earn the arrival flash, as of
 *  `now`?
 *
 *  BOTH HALVES ARE REQUIRED.
 *
 *  `ts > mountedAt` is the half that was always here: a message already on the
 *  server when this list opened is not an arrival, so opening a thread never
 *  lights every row at once. Reading it off the message's own timestamp rather
 *  than a "new" flag is what keeps that honest across a reload — a message
 *  another tab wrote an hour ago is not news to this reader's eye.
 *
 *  `now - ts < windowMs` is the half that makes it a MOMENT instead of a
 *  property. The flash is spent by the time it has played, so nothing can
 *  replay it later. A clock that reads the message as slightly in the future
 *  (skew, or a message written in the same second) still flashes: the test is
 *  on how long ago it was, and "not yet" is not "long ago". */
export function flashesOnArrivalAt(
  at: number | undefined,
  mountedAt: number,
  now: number,
  windowMs: number = ARRIVAL_FLASH_MS,
): boolean {
  if (typeof at !== "number" || !Number.isFinite(at)) return false;
  if (at <= mountedAt) return false;
  return now - at < windowMs;
}

/** The same rule over a message's ISO `ts`. An unparseable stamp never
 *  flashes: a flash is an assertion about when something happened, and there
 *  is nothing here to assert it from. */
export function flashesOnArrival(
  ts: string | undefined,
  mountedAt: number,
  now: number,
  windowMs: number = ARRIVAL_FLASH_MS,
): boolean {
  if (!ts) return false;
  return flashesOnArrivalAt(Date.parse(ts), mountedAt, now, windowMs);
}
