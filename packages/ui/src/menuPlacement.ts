// WHERE A POPOVER OPENS, as arithmetic with no DOM in it.
//
// Split out of Menu so it can be executed by a test. The kit has no render
// harness and is not getting one; the house rule is that the PURE RULE behind a
// behaviour moves into its own module so it can be run. This is that rule for
// menu placement, and it is worth having: the bug that prompted it was a wrong
// answer to one comparison, in a browser, where nothing could reach it.
//
// Two anchored surfaces now share it, Menu and Popover, through the
// `useAnchoredPlacement` hook that holds the DOM half.
//
// THE BUG: the flip measured the list against the VIEWPORT and nothing else, so
// a menu inside a clipping scroller opened outward and was sliced off at the
// scroller's edge while the viewport still had room. In the chat that is exactly
// the geometry — `.chat-log` is `overflow-x: hidden` and the column is capped at
// `--page-max` and centred — so the branch control's menu opened rightward off a
// bubble and half of it was simply not there. Reported 2026-08-14.

export type Rect = { left: number; top: number; right: number; bottom: number };

/** One ancestor that clips, and on which axes. */
export type Clipper = { rect: Rect; clipsX: boolean; clipsY: boolean };

/**
 * Narrow a starting box (the viewport) by every ancestor that clips.
 *
 * The DOM half of this — walking parentElement and asking getComputedStyle
 * which axes clip — lives in `useAnchoredPlacement`. This half is the rule, and
 * it is a rule worth executing: each axis is narrowed SEPARATELY, because
 * `overflow-x: hidden` with a `visible` y is a real and common combination
 * (`.chat-log` is exactly it) and clipping one axis says nothing about the
 * other. Collapsing the two into one "does it clip" test is what would put a
 * menu back off the edge of that log.
 */
export function narrowClip(base: Rect, clippers: readonly Clipper[]): Rect {
  const box = { ...base };
  for (const { rect, clipsX, clipsY } of clippers) {
    if (clipsX) {
      box.left = Math.max(box.left, rect.left);
      box.right = Math.min(box.right, rect.right);
    }
    if (clipsY) {
      box.top = Math.max(box.top, rect.top);
      box.bottom = Math.min(box.bottom, rect.bottom);
    }
  }
  return box;
}

export type Placement = {
  /** Pixel offset from the trigger's own left edge to the surface's left
   * edge. Not a binary "pin to whichever edge fits": a surface whose width
   * is close to the clip box's own width (Popover's ref-card panel is
   * 18-24rem, a real fraction of a phone's viewport) can overflow BOTH edges
   * depending on where the trigger sits, and picking the lesser of two fixed
   * anchor points cannot fix that -- only sliding the surface between them
   * can. A chat ref chip, unlike a toolbar button pinned near a screen edge,
   * can land anywhere across a wrapped line, including the middle of a
   * narrow phone screen. Reported 2026-08-19: a doc/note/ticket chip's
   * dropdown opened "slightly hanging off the screen to the side" on mobile.
   */
  insetStart: number;
  /** Which side of the trigger it opens on. */
  side: "bottom" | "top";
};

/** Kept clear of the clip edge so the popover never looks welded to it. */
const EDGE_PAD = 8;

/**
 * Both placements are PROJECTED from the trigger and the list's own size, never
 * read back from where the list currently sits. Reading the placed rect means
 * the second measurement sees the result of the first: a menu that had flipped
 * to `end` measured its flipped position, found no overflow, and stayed flipped
 * forever even after the window grew back. Projection gives the same answer
 * every time for the same geometry, which is what makes re-measuring on scroll
 * and resize safe.
 *
 * @param trigger the control's rect, in viewport coordinates.
 * @param size the list's own width and height (already clamped by its CSS
 *   max-width / max-height, so a long menu is measured at the size it will
 *   actually be, scrolling inside).
 * @param clip the box it must stay inside: the viewport narrowed by every
 *   ancestor that clips. See `narrowClip` above and its caller in
 *   useAnchoredPlacement.ts.
 * @param side the caller's PREFERRED side. A preference, not a guarantee: the
 *   caller knows which side reads better, not how much room the page will have
 *   at that scroll position on that viewport.
 * @param gap the distance the CSS leaves between trigger and list.
 */
export function placeMenu(
  trigger: Rect,
  size: { width: number; height: number },
  clip: Rect,
  side: "bottom" | "top",
  gap: number,
): Placement {
  // Left-flush with the trigger is the preference -- same default as before.
  // But it is a STARTING point now, clamped into the box the surface must
  // stay inside, rather than one of exactly two fixed anchor points. Pinning
  // to whichever of the trigger's two edges overflows less is only correct
  // when the surface is comfortably narrower than the clip box (a toolbar
  // menu near a screen edge); once the surface approaches the clip box's own
  // width, a trigger anywhere in the MIDDLE overflows past BOTH edges no
  // matter which one it is pinned to, and no binary choice fixes that.
  const lo = clip.left + EDGE_PAD;
  const hi = clip.right - EDGE_PAD - size.width;
  // hi < lo means the surface is wider than the box has room for at all: no
  // position avoids overflow. Anchor to the clip's own left edge rather than
  // the trigger's, which overflows by exactly (width - available room) on
  // the right and nowhere else, instead of an overflow whose size depends on
  // an arbitrary trigger position.
  const left = hi >= lo ? Math.min(Math.max(trigger.left, lo), hi) : lo;

  // Only flip vertically when the preference is genuinely clipped AND the
  // other way has room. A surface too tall for either side stays where the
  // caller asked for it and scrolls, rather than thrashing between two bad
  // placements.
  const belowOverflows = trigger.bottom + gap + size.height > clip.bottom - EDGE_PAD;
  const aboveOverflows = trigger.top - gap - size.height < clip.top + EDGE_PAD;

  return {
    insetStart: left - trigger.left,
    side: side === "bottom"
      ? (belowOverflows && !aboveOverflows ? "top" : "bottom")
      : (aboveOverflows && !belowOverflows ? "bottom" : "top"),
  };
}
