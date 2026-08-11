// What changing the scope picker should DO to the open conversation.
//
// The scope is a property OF a chat, so changing it re-binds that chat. It does
// not navigate away from it. This used to only set `activeProvider`, and the
// thread list is fetched per scope, so switching re-listed a different scope's
// threads, found none in a fresh scope, and opened a blank one. The
// conversation the operator was having appeared to be deleted. He reported it
// twice as a design flaw, most recently on first opening `development`.
//
// chat-scope-design-2026-07-18.md says the same ("changing the selection calls
// rescopeChat"), and POST /threads/:id/rescope already does the right work:
// same id, same transcript, new context_key, live session dropped so the next
// turn re-assembles with the new persona. Nothing called it.
//
// Extracted as a pure function because that is the testable unit here: there is
// no DOM harness, so the RULE lives in its own module and the component just
// obeys it.

export type ScopeSwitch =
  /** Same scope. Nothing to do, and no request to make. */
  | { action: "noop" }
  /** Nothing worth moving, so just point the picker somewhere else. */
  | { action: "switch" }
  /** Move this conversation, keeping its id and history. */
  | { action: "rescope" }
  /** Refuse, and say why. */
  | { action: "refuse"; reason: string };

export function scopeSwitch(input: {
  target: string;
  current: string;
  /** Does the open thread exist and have any committed events? */
  hasHistory: boolean;
  /** Is a turn in flight on it? */
  runActive: boolean;
}): ScopeSwitch {
  const { target, current, hasHistory, runActive } = input;
  if (!target || target === current) return { action: "noop" };
  // Order matters: an EMPTY chat mid-run is still just an empty chat to move,
  // but there is nothing to move, so switching is both safe and correct. Only a
  // real conversation is worth refusing over.
  if (!hasHistory) return { action: "switch" };
  if (runActive) {
    // Rescope clears the live session the run is speaking through. Doing that
    // underneath a turn breaks it, so this refuses rather than corrupting the
    // run or silently ignoring the click.
    return {
      action: "refuse",
      reason: "This chat is mid-turn. Stop it or let it finish before changing scope.",
    };
  }
  return { action: "rescope" };
}
