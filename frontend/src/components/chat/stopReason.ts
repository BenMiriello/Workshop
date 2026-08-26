// Why a reply stopped short, in one plain word.
//
// Two different things end a turn early and they are not the same event:
//
//   - the operator pressed Stop. They ended it, deliberately, and the work above
//     the tag is what it got through before they did.
//   - the run did not finish on its own. Nobody chose that.
//
// The backend marks which with `meta.stopped` (services/chat_store:
// STOPPED_BY_OPERATOR / STOPPED_INTERRUPTED). Blurring them into one sentence
// was the old behaviour and it read as a fault every time the operator had
// simply stopped the turn themselves. The copy names neither the cause nor any
// internal mechanism: "interrupted by a restart" leaked how the platform works
// into a line the operator reads, which is not what a status line is for.

/** The operator pressed Stop. Mirrors chat_store.STOPPED_BY_OPERATOR. */
export const STOPPED_BY_OPERATOR = "operator";

/** A restart orphaned the run. Mirrors chat_store.STOPPED_INTERRUPTED. */
export const STOPPED_INTERRUPTED = "interrupted";

/** The one quiet line UNDER a reply that did not run to completion.
 *
 *  Three words, no instruction. The operator who pressed Stop knows they can
 *  send another message; telling them so is the app narrating itself. Longer
 *  wording was tried ("You stopped this turn. Send a message to continue.")
 *  alongside a border and a tag above the body, and the whole treatment was
 *  three statements of one small fact. */
export function stoppedTag(stopped: string): string {
  return stopped === STOPPED_BY_OPERATOR ? "Stopped" : "Interrupted";
}
