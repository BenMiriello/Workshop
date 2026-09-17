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

// ---- THE SPENT SET ---------------------------------------------------------
//
// THE TIME WINDOW ALONE IS NOT A ONE-SHOT, and that is the defect the operator
// kept re-reporting. A CSS animation replays whenever the element carrying its
// class is CREATED, and the window above says "flash if this message is less
// than ARRIVAL_FLASH_MS old". Those two facts together mean a message inside
// its window re-animates EVERY time its node is rebuilt, however many times
// that happens. The 2026-09-04 fix removed the permanent-property version of
// this bug and left the transient one, which is smaller but not gone: it no
// longer replays a whole hour of transcript, it replays the last few seconds
// of it.
//
// That is exactly what a long agentic turn produces. Messages land in bursts a
// second or two apart, so at any instant during one, SEVERAL messages are
// inside the window at once. Reported again 2026-09-16: "every time I reopen
// the chat or like close the app and reopen the chat here it sort of replays
// animating the last several messages". Same defect, third report.
//
// A WINDOW CANNOT FIX THIS, at any width, and that is why this is a set rather
// than a smaller number. Shrinking the window shrinks the burst it replays; it
// never reaches zero, because the window has to be wide enough to cover the
// clock skew the comment above describes. The only thing that makes a flash
// unrepeatable is REMEMBERING that it already played.
//
// MODULE SCOPE, NOT COMPONENT STATE, because the whole point is to outlive the
// component. Every way the list gets rebuilt (a remount, a route change, the
// drag overlay replacing the log, a thread going blank and coming back, keys
// changing as a pending id is replaced by a server id) is a case where
// component-local memory is discarded and the animation is not.
//
// A FULL PAGE RELOAD DELIBERATELY CLEARS IT, and that is correct rather than a
// limitation: on a reload `mountedAt` is the load instant, so every message
// already in the log fails the `at <= mountedAt` half and nothing flashes. The
// set only has to cover the cases where `mountedAt` is older than the nodes.
const _spent = new Set<string>();

// Bounded so a very long session cannot grow this without limit. A Set iterates
// in insertion order, so the oldest ids are the ones dropped -- and an id old
// enough to be dropped is far outside the window anyway, so dropping it cannot
// resurrect a flash.
const _SPENT_CAP = 1000;

/** Has this message's arrival flash already played in this page session? */
export function flashSpent(id: string): boolean {
  return _spent.has(id);
}

/** Record that it played. Call AFTER the render commits, never during one: a
 *  render can be discarded (StrictMode's double render, a concurrent render
 *  that is thrown away) and spending a flash for a frame that never reached the
 *  screen would silently delete the animation instead of replaying it. */
export function spendFlash(id: string): void {
  if (_spent.has(id)) return;
  _spent.add(id);
  if (_spent.size > _SPENT_CAP) {
    const oldest = _spent.values().next();
    if (!oldest.done) _spent.delete(oldest.value);
  }
}

/** Test seam. Nothing in the app calls this: the set is deliberately process
 *  lifetime, and a test that shares it with the next test is a test that passes
 *  for the wrong reason. */
export function _resetSpentFlashes(): void {
  _spent.clear();
}

/** The whole rule, as one question: does this message earn the flash NOW?
 *
 *  Time window AND not already spent. Pure -- it reads the set and does not
 *  write it, so it is safe to call during a render. `spendFlash` is the write
 *  half and belongs in an effect. */
export function flashesOnArrivalOnce(
  id: string,
  ts: string | undefined,
  mountedAt: number,
  now: number,
  windowMs: number = ARRIVAL_FLASH_MS,
): boolean {
  if (flashSpent(id)) return false;
  return flashesOnArrival(ts, mountedAt, now, windowMs);
}
