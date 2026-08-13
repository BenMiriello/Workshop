// WHERE A CHAT OPENS TO, and the two different questions that look like one.
//
// Both ask "is this at the bottom", and they are NOT the same question, which is
// why they get two numbers instead of sharing one:
//
//   FOLLOW   should the log keep chasing new content as it arrives?
//            Tight (FOLLOW_SLOP). Scroll up a little to re-read the last message
//            mid-turn and the next token must not yank you back down.
//
//   RESTORE  you are reopening this thread. Put you back where you were, or at
//            the latest?
//            Loose (half a viewport). Being a few hundred pixels off the bottom
//            when you left says nothing about where you want to land when you
//            come back; it almost always means "I was reading the end of it".
//
// Operator, 2026-08-13: "if I were at or within 1/2 a view height of the bottom
// of a chat last time viewing then I come back we should treat that like I was
// viewing the bottom, and instead just place me at viewing the bottom/latest,
// not wherever I was before."
//
// THE BOTTOM IS ALSO THE ONLY GROWTH-PROOF TARGET, which is why widening this
// fixes more than it was asked to. A saved position is an absolute `scrollTop`,
// and a transcript keeps growing after first paint (clamps settle, markdown
// reflows, more of the log arrives). Restoring `Math.min(top, max)` against a
// half-rendered thread lands mid-thread once the rest shows up. "The bottom"
// re-reads `max` every frame and cannot drift, so every position that resolves
// to it is immune to the whole class of bug.

export type ScrollPos = { top: number; atBottom: boolean };

/** Distance from the bottom, in px, that still counts as "following". */
export const FOLLOW_SLOP = 80;

/** Should reopening this thread land at the latest rather than at the saved
 *  offset?
 *
 *  Pure, in plain numbers, so the rule is executable rather than only observable
 *  by reopening chats and watching where they land.
 *
 *  `null` (nothing saved) means the bottom: a thread you have never scrolled is
 *  a thread you want the end of. */
export function restoresToBottom(
  saved: ScrollPos | null, scrollHeight: number, clientHeight: number,
): boolean {
  if (!saved || saved.atBottom) return true;
  const max = Math.max(0, scrollHeight - clientHeight);
  const top = Math.min(Math.max(0, saved.top), max);
  return max - top < clientHeight / 2;
}
