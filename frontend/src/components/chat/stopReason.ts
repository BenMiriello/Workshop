// Why a reply stopped short, in the operator's words.
//
// Two different things end a turn early and they are not the same event:
//
//   - the operator pressed Stop. They ended it, deliberately, and the work above
//     the tag is what it got through before they did.
//   - the backend went down under the run. Nobody chose that, and the thing to
//     say is that anything already written to disk is intact.
//
// The backend marks which with `meta.stopped` (services/chat_store:
// STOPPED_BY_OPERATOR / STOPPED_INTERRUPTED). Blurring them into one sentence
// was the old behaviour and it read as a fault every time the operator had
// simply stopped the turn themselves.

/** The operator pressed Stop. Mirrors chat_store.STOPPED_BY_OPERATOR. */
export const STOPPED_BY_OPERATOR = "operator";

/** A restart orphaned the run. Mirrors chat_store.STOPPED_INTERRUPTED. */
export const STOPPED_INTERRUPTED = "interrupted";

/** The tag above a reply that did not run to completion. One line, said once:
 *  who ended it, and what to do next. */
export function stoppedTag(stopped: string): string {
  return stopped === STOPPED_BY_OPERATOR
    ? "You stopped this turn. Send a message to continue."
    : "Turn interrupted. Send a message to continue.";
}

/** The body of a stopped reply that has nothing in it. Answers the one question
 *  the tag leaves open: was anything written before it ended? An empty bubble
 *  reads as a rendering bug rather than as a turn that ended early. */
export function stoppedNoReplyText(stopped: string): string {
  return stopped === STOPPED_BY_OPERATOR
    ? "No reply was written before you stopped it."
    : "No reply was written before the turn was interrupted.";
}
