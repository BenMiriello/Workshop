import { useLayoutEffect, useState, type RefObject } from "react";
import { narrowClip, placeMenu, type Clipper, type Rect } from "./menuPlacement";

// THE DOM HALF OF ANCHORED PLACEMENT, shared by Menu and Popover.
//
// Both are a surface pinned to a trigger that has to flip when the preferred
// placement would be clipped, and there is exactly one correct answer to
// "where does it open", so there is one implementation of it. This hook turns
// the DOM into the four arguments `placeMenu` takes and re-asks the question
// whenever the geometry can have moved; the arithmetic itself stays in
// menuPlacement.ts, where a test can execute it.
//
// It was Menu's private layout effect until Popover needed the same behaviour.
// Copying it would have meant two copies of the flip rule, and the flip rule is
// the thing that has already been got wrong once (see menuPlacement.ts).

// The box a popover must stay inside: the viewport, narrowed by every ancestor
// that clips.
//
// THE VIEWPORT IS NOT THE ONLY EDGE, and assuming it was is what put the chat's
// branch menu half off screen. `.chat-log` is `overflow-x: hidden` and the chat
// column is capped at `--page-max` and centred, so on a desktop a menu opening
// from a bubble had hundreds of pixels of viewport to its right and was still
// sliced off at the log's own edge. Measured against the viewport it did not
// overflow, so it never flipped. Reported 2026-08-14: "it opens up and off to
// the side oddly and is clipped/hidden off the edge of its container".
//
// Each axis is asked separately, because `overflow-x: hidden` with a `visible`
// y is a real and common combination (that log is exactly it) and clipping one
// axis says nothing about the other. That reduction is `narrowClip`, which is
// pure and tested; this function only reads the DOM.
function clipBox(el: HTMLElement): Rect {
  const clippers: Clipper[] = [];
  for (let n = el.parentElement; n; n = n.parentElement) {
    const s = getComputedStyle(n);
    // CSS coerces a `visible` paired with a non-visible other axis to `auto`,
    // so a computed value of `visible` is the only one that does not clip.
    const clipsX = s.overflowX !== "visible";
    const clipsY = s.overflowY !== "visible";
    if (!clipsX && !clipsY) continue;
    const r = n.getBoundingClientRect();
    clippers.push({
      rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom },
      clipsX,
      clipsY,
    });
  }
  return narrowClip(
    { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight },
    clippers,
  );
}

/**
 * Where an anchored surface should open, re-measured while it is open.
 *
 * @param open whether the surface is mounted. Closed, the placement resets to
 *   the caller's preference, so the next open never inherits the last flip.
 * @param side the PREFERRED side. A preference, not a guarantee.
 * @param anchorRef the positioned wrapper holding the trigger.
 * @param surfaceRef the surface itself, mounted only while open.
 */
export function useAnchoredPlacement(
  open: boolean,
  side: "bottom" | "top",
  anchorRef: RefObject<HTMLElement | null>,
  surfaceRef: RefObject<HTMLElement | null>,
): { align: "start" | "end"; side: "bottom" | "top" } {
  // Horizontal alignment is NOT a caller choice — it used to be (`align`
  // start|end per instance), and every caller had to reason about where in its
  // row it sat and guess whether that made left or right the safe direction.
  // Two guesses were wrong on a narrow phone (Effort and Mode, both hardcoded
  // "end", ran off the LEFT edge). Left-anchored ("start") is the one default;
  // this measures itself after opening and flips to right-anchored ("end")
  // only if that default would run past the right edge of the box it must stay
  // inside. One rule, no per-caller guessing, correct at any position in any
  // row.
  const [align, setAlign] = useState<"start" | "end">("start");
  // Where it ACTUALLY opened, which is `side` unless that would be clipped.
  const [placed, setPlaced] = useState<"bottom" | "top">(side);

  // Measure BEFORE paint, so a flip is never visible as a jump. Re-measures on
  // resize and on any ancestor scroll while open: the trigger's position
  // relative to the clip edge can change without a close and reopen.
  useLayoutEffect(() => {
    if (!open) { setAlign("start"); setPlaced(side); return; }
    const measure = () => {
      const surface = surfaceRef.current;
      const anchor = anchorRef.current;
      if (!surface || !anchor) return;
      // The arithmetic is in menuPlacement.ts, where a test can execute it.
      // This function's whole job is turning the DOM into its four arguments.
      const at = anchor.getBoundingClientRect();
      const p = placeMenu(
        { left: at.left, top: at.top, right: at.right, bottom: at.bottom },
        { width: surface.offsetWidth, height: surface.offsetHeight },
        clipBox(anchor),
        side,
        // The real gap the CSS leaves between trigger and surface, read from
        // the token rather than restated as a number that can drift from it.
        parseFloat(getComputedStyle(surface).getPropertyValue("--space-1")) || 4,
      );
      setAlign(p.align);
      setPlaced(p.side);
    };
    measure();
    window.addEventListener("resize", measure);
    // Scrolling any ancestor moves the trigger relative to the clip box without
    // closing the surface (only a pointerdown does that), so the side that
    // fitted when it opened may not fit a moment later. Capture, because a
    // scroll on an inner scroller does not bubble.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, side, anchorRef, surfaceRef]);

  return { align, side: placed };
}
