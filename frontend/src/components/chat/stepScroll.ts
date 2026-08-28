// A TOOL GROUP DOES NOT GROW WITHOUT LIMIT.
//
// Measured over the operator's own logs (148 threads, 632 replies, 7,914 runs
// of adjacent tool calls): the median run is 2 steps, but 188 runs exceed 10
// and the longest is 173. An opened group of 173 rows is not a disclosure, it
// is a second scrollbar's worth of transcript wearing one row's affordance, and
// on a phone it buries the answer under a screenful of `Bash` lines.
//
// So past CAP_AFTER steps the body becomes its own scroll region, CAP_ROWS tall.
// Every step stays reachable -- nothing is dropped, nothing is summarised, and
// the count in the header still counts all of them. This is the same choice the
// group header already made: a cap cannot misdescribe what it capped.
//
// WHY 11 ROWS AND NOT 10. The cap is one row TALLER than the threshold that
// triggers it, so a capped region always shows a partial row at its edge. That
// half-row is the affordance: it is what says "there is more below" without a
// gradient, a badge, or a count nobody reads. A region capped at exactly the
// trigger height looks like a complete list that happens to end there.
//
// THE SCROLL RULE IS THE PART THAT IS EASY TO GET WRONG, and it is an explicit
// failure condition rather than a nicety. Operator, 2026-08-27: "if you scroll
// up ... it shouldn't just keep resetting you back down to the bottom".
//
// Follow the newest step ONLY while the reader is already at the bottom. The
// moment they scroll up they own the position, and nothing automatic moves it
// again -- not a new step arriving, not a step below them being expanded. This
// is the same provenance rule disclosure.ts applies to open/closed, applied to
// scroll offset: something automatic may only move what the machine itself last
// moved. It is also the rule every reference implementation uses (shadcn's
// Message Scroller, OpenHands' use-scroll-to-bottom, GitLab DuoChat), and the
// bug it exists to prevent is the one every one of them filed first.
//
// Pure and total: no DOM, no React, no effects. The rule is a function of
// measurements, so it is pinned in stepScroll.test.ts rather than only
// observable by watching a 173-step turn stream past.

/** Steps past which a group's body becomes a capped scroll region. */
export const CAP_AFTER = 10;

/** How many rows tall that region is. One more than CAP_AFTER, so the partial
 *  row at the edge is what shows there is more. */
export const CAP_ROWS = 11;

/** How close to the bottom still counts as "at the bottom", in px.
 *
 *  Not zero. Sub-pixel layout, fractional device pixel ratios and a row growing
 *  by a fraction as a font settles all leave `scrollTop` a hair short of the
 *  exact bottom, and a zero-tolerance test reads that as "the reader scrolled
 *  up" -- which silently turns following OFF for a reader who never touched
 *  anything. Erring toward following is the safe direction here: the cost of a
 *  false positive is one unwanted scroll, the cost of a false negative is a
 *  region that stops following forever and looks frozen. */
export const BOTTOM_EPSILON = 4;

/** What a scroll container looks like to this module. The three numbers every
 *  DOM element already exposes, so a caller passes the element's own values and
 *  nothing here needs a DOM. */
export type ScrollMetrics = {
  /** Current offset from the top. */
  scrollTop: number;
  /** Visible height of the region. */
  clientHeight: number;
  /** Total height of the content in it. */
  scrollHeight: number;
};

/** Does this group's body need capping at all.
 *
 *  Under the threshold a group renders exactly as it always did: no scroll
 *  region, no max-height, no behaviour to get wrong. That is the overwhelming
 *  majority of groups (the median run is 2 steps), and they must not pay for
 *  the long tail. */
export function isCapped(stepCount: number): boolean {
  return stepCount > CAP_AFTER;
}

/** Is the reader at the bottom of the region.
 *
 *  True for a region that does not scroll at all (content shorter than the
 *  viewport): there is no "up" to have scrolled to, so a group that has not yet
 *  overflowed its cap follows by default and keeps following as it fills. */
export function atBottom(m: ScrollMetrics, epsilon = BOTTOM_EPSILON): boolean {
  return m.scrollHeight - m.clientHeight - m.scrollTop <= epsilon;
}

/** Where the bottom is, for a caller about to set `scrollTop`. */
export function bottomOf(m: ScrollMetrics): number {
  return Math.max(0, m.scrollHeight - m.clientHeight);
}

/** Whether an automatic scroll-to-bottom is allowed right now.
 *
 *  `following` is the sticky state the caller carries between renders, set from
 *  `atBottom` on the reader's own scroll events. It is deliberately NOT
 *  recomputed from the metrics here: by the time new content has been laid out,
 *  the reader's old position is no longer at the bottom BECAUSE the content
 *  grew, and testing the post-growth metrics would answer "not at the bottom"
 *  for a reader who never moved and would turn following off exactly once,
 *  permanently, on the first new step. That inversion is the classic form of
 *  this bug. The state is sampled BEFORE the growth and consulted after. */
export function shouldFollow(following: boolean): boolean {
  return following;
}

/** The reader scrolled. Returns the new sticky state.
 *
 *  This is the ONLY thing that turns following back on, and it does so simply by
 *  their arriving at the bottom again -- no button, no timeout, no gesture
 *  detection. Scrolling back down to the newest step means you want the newest
 *  step, which is what following is. */
export function onReaderScroll(m: ScrollMetrics, epsilon = BOTTOM_EPSILON): boolean {
  return atBottom(m, epsilon);
}
