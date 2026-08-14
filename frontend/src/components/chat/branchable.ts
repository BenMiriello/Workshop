// WHICH BUBBLE OFFERS TO GO BACK TO IT, and in which sense (ADR 0020).
//
// THE RULE IS ONE QUESTION: would going back to this message produce a
// conversation different from the one on screen? If not, there is nothing to
// offer, and the control is not rendered. Everything below is that question
// applied to the cases the transcript actually contains.
//
// It replaces a rule that rendered the control everywhere and DISABLED it where
// it would do nothing. The operator asked the obvious question of the result:
// "why would it be there for the last message if nothing else is after?" There
// was no good answer. A disabled control is a promise that the action exists
// here and is unavailable right now; on the last reply it would never be
// available, because there is no state for it to produce. That is not a
// disabled action, it is an absent one.
//
// TWO SENSES, AND WHOSE MESSAGE IT IS DECIDES WHICH:
//
//   assistant  "after": keep this answer, drop what came after it. Produces
//              m1..mi, which differs from the current transcript only when this
//              is not the last message. On the last reply it is the identity
//              operation, so the last reply carries no control.
//
//   user       "before": take back what you said here. The anchor is the
//              message BEFORE it, so it produces m1..m(i-1) — always one shorter
//              than what is on screen, INCLUDING when it is the last message.
//              Pointing at your own words and continuing FROM them would leave
//              you typing into your own last word, which is not what pointing at
//              them means.
//
// So "the last bubble has no control" is not the rule; it is what the rule
// happens to produce for a trailing reply. A trailing message of your own keeps
// its control, and that case (take back what I just sent and say it differently)
// is one of the most useful in the feature.

/** Which sense the action has on this bubble. See the note above. */
export type BranchMode = "after" | "before";

/** The only fields the rule reads. `id` is the log event; `key` is the block,
 *  and one event can produce several blocks. */
export type BranchBlock = { key: string; id: string; role: string };

/**
 * The blocks that offer to go back to them, keyed by BLOCK key, valued by the
 * sense of the action there.
 *
 * @param blocks committed message blocks, in render order. Pending, queued and
 *   live blocks must not be passed: none of them is on the server as a point in
 *   the conversation, so none is a place to return to. They render through
 *   different components, so this is a precondition rather than a filter.
 * @param head the branch's last committed message id.
 */
export function branchTargets(blocks: BranchBlock[], head: string | null): Map<string, BranchMode> {
  // ONE CONTROL PER EVENT, ON ITS LAST BLOCK.
  //
  // A reply that was interrupted renders as several blocks that all came from
  // one log event and therefore all resolve to the same anchor. A control on
  // each would show three identical buttons doing one identical thing, reading
  // as three branch points when there is one. The last block is also where the
  // turn's own metadata already goes, so they stay together.
  const lastBlockOfEvent = new Map<string, BranchBlock>();
  for (const b of blocks) lastBlockOfEvent.set(b.id, b);

  const out = new Map<string, BranchMode>();
  for (const b of lastBlockOfEvent.values()) {
    const mode: BranchMode = b.role === "user" ? "before" : "after";
    // The identity operation. Only reachable in "after": see the note above.
    if (mode === "after" && head !== null && b.id === head) continue;
    out.set(b.key, mode);
  }
  return out;
}
