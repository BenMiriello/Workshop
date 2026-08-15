// HOW MUCH OF THE VIEWPORT THE SOFT KEYBOARD IS COVERING.
//
// One number, and it is not as simple as it sounds, because browsers disagree
// about WHAT THE KEYBOARD RESIZES. There are two models:
//
//   resizes-visual   the layout viewport keeps its height and only the VISUAL
//                    viewport shrinks. `window.innerHeight` is unchanged;
//                    `visualViewport.height` drops by the keyboard. (iOS Safari,
//                    and the spec default.)
//   resizes-content  BOTH shrink: the page is laid out in the space that is
//                    left. `innerHeight` drops too. (Chrome on Android when the
//                    document asks for it — and this document does, in
//                    index.html: `interactive-widget=resizes-content`.)
//
// The first implementation measured `innerHeight - visualViewport.height`, which
// is exactly right under the first model and IDENTICALLY ZERO under the second,
// because both sides shrink together. So on the operator's phone the keyboard
// height was always 0, the composer's bottom padding never collapsed, and the
// dead row above the keyboard came back. Reported 2026-08-14 as a regression,
// and it was one: the rule it replaced (`.chat-composer.is-focused`) keyed off
// focus, which is a bad proxy for the keyboard but at least a portable one.
//
// A measurement that reads as "no keyboard" when it is wrong is the worst shape
// this could have: nothing looks broken, the layout is simply never told. So the
// rule lives here, as a function over numbers, with the two models and the
// awkward cases pinned in keyboardInset.test.ts. Getting it wrong now fails a
// test instead of silently reporting zero.
//
// WHAT WORKS UNDER BOTH: compare the viewport against its own RESTING height
// rather than against the other viewport. The keyboard shrinks the visible
// height under either model, so `resting - current` is the keyboard either way.

/** One measurement of the window, as the hook reads it. */
export type ViewportReading = {
  /** `visualViewport.height`, or `innerHeight` where there is no visualViewport. */
  viewportHeight: number;
  /** `window.innerHeight`. Differs from the above only under resizes-visual. */
  innerHeight: number;
  /** `window.innerWidth`, which is how a rotation is noticed. */
  width: number;
};

/** The resting height for one orientation. Null until the first reading. */
export type ViewportBaseline = { width: number; height: number } | null;

/** A keyboard is never shorter than this. Browser chrome is: hiding the URL bar
 *  grows the viewport by roughly 50-60px, and that must not read as a keyboard
 *  closing. Every soft keyboard on every phone is well over 200px, so the gap
 *  between the two is wide and this threshold sits in the middle of it. */
export const MIN_KEYBOARD_PX = 120;

/** The resting height, updated by a reading.
 *
 *  The tallest height seen for the CURRENT WIDTH. Tallest, because the keyboard
 *  only ever makes it shorter; per width, because a rotation changes the resting
 *  height completely and a portrait baseline would read as a permanent keyboard
 *  in landscape. */
export function updateBaseline(prev: ViewportBaseline, r: ViewportReading): ViewportBaseline {
  if (!prev || prev.width !== r.width) return { width: r.width, height: r.viewportHeight };
  if (r.viewportHeight > prev.height) return { width: r.width, height: r.viewportHeight };
  return prev;
}

/** How many CSS pixels of the viewport the keyboard is covering, or 0.
 *
 *  Two independent estimates, and the larger wins:
 *
 *    * against the resting height — correct under BOTH models, and the only one
 *      that works under resizes-content;
 *    * against `innerHeight` — correct under resizes-visual, and it needs no
 *      baseline, which is what makes the answer right immediately when the hook
 *      mounts with the keyboard ALREADY open (opening a chat from a screen where
 *      something was focused). With only the baseline, that case reads as zero
 *      until the keyboard is closed once.
 *
 *  Below the threshold the answer is 0: browser chrome moves the viewport by
 *  tens of pixels routinely, and reporting that as a keyboard would collapse the
 *  composer's padding while nothing is on screen to collapse it for. */
export function keyboardInset(r: ViewportReading, baseline: ViewportBaseline): number {
  const fromBaseline = baseline && baseline.width === r.width
    ? baseline.height - r.viewportHeight
    : 0;
  const fromInner = r.innerHeight - r.viewportHeight;
  const inset = Math.max(fromBaseline, fromInner, 0);
  return inset >= MIN_KEYBOARD_PX ? Math.round(inset) : 0;
}
