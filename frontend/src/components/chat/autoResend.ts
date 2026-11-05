import type { PendingSend } from "./threadStore";
import { unsentSends } from "./unsentMessages";

// SENDING AGAIN WHEN THE CONNECTION COMES BACK.
//
// A send that exhausted its transient retries sat in the outbox until the
// operator pressed Retry, so a backend restart longer than those retries lost
// a message the operator believed was sent. Every send carries a client id the server
// dedupes on, so re-sending the same id is safe: a copy that did land is
// reconciled rather than repeated.
//
// Two things fire it, the change stream reopening and the window's `online`
// event, and they routinely fire together. Neither fires for a backend that
// restarted under a connection that never dropped, or stalled, so the page also
// fires it itself: on a clock while a send is owed, and when the window is
// focused or shown. Those passes take only the sends that failed TRANSIENTLY: a
// message the server refused is not re-sent every few seconds behind the
// operator's back. The gate below makes any number of triggers one pass:
// a trigger that arrives while a pass is running does not start a second one,
// it asks for ONE more pass after this one, over whatever is still unsent. A
// message that went out in the first pass is not in the second, so nothing is
// ever sent twice by this, and a failure the first pass hit before the backend
// was really up gets another go from the trigger that proved it was.

export type ResendGate = {
  running: boolean;
  /** A trigger arrived during the current pass. */
  again: boolean;
};

export const RESEND_IDLE: ResendGate = { running: false, again: false };

/** A trigger fired. `start` says whether to begin a pass now. */
export function resendTriggered(g: ResendGate): { next: ResendGate; start: boolean } {
  if (g.running) return { next: { running: true, again: true }, start: false };
  return { next: { running: true, again: false }, start: true };
}

/** A pass ended. `start` says whether to run the one it owes. */
export function resendPassEnded(g: ResendGate): { next: ResendGate; start: boolean } {
  if (g.again) return { next: { running: true, again: false }, start: true };
  return { next: RESEND_IDLE, start: false };
}

/** What a pass sends, oldest first. Only failed sends: one still `sending` has
 *  a request in flight already. `onlyTransient` is a pass the page started on
 *  its own, which leaves a refused message alone. */
export function resendOrder(pending: PendingSend[], onlyTransient = false): string[] {
  return unsentSends(pending)
    .filter((s) => !onlyTransient || s.transient === true)
    .map((s) => s.clientId);
}

/** How often the clock looks for an owed send when there was none. */
export const RESEND_IDLE_CHECK_MS = 2000;

/** The wait before the next pass the clock starts, after `attempt` of them
 *  have left a send owed: 2s doubling, capped at 30s. */
export function resendBackoff(attempt: number): number {
  return Math.min(2000 * 2 ** attempt, 30_000);
}

/** Send each in order, one at a time, and STOP AT THE FIRST FAILURE. Carrying
 *  on past it would deliver a later message ahead of an earlier one, and the
 *  failure says the server is not taking sends yet anyway. Returns how many
 *  went. */
export async function resendInOrder(
  ids: string[],
  send: (clientId: string) => Promise<boolean>,
): Promise<number> {
  let sent = 0;
  for (const id of ids) {
    if (!(await send(id))) break;
    sent += 1;
  }
  return sent;
}
