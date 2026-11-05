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
// THE SURFACE IS `position: fixed`, so it floats over whatever scrolls or
// clips around its trigger instead of living inside it. It used to be
// absolute, and a scroller's overflow was then both its cage and its
// scrollbar: a row menu near the end of a short list did not fit either way,
// opened anyway, grew the list's scroll height and was scrolled into view by
// its own focus, so the whole list jumped up under it. Reported 2026-11-03 on
// Browser's downloads list. The surface stays where it is in the DOM, so
// every descendant rule a consumer wrote against it still matches.

// Where a fixed surface placed at (0, 0) actually lands. The viewport's
// corner, unless an ancestor is the containing block for fixed descendants
// (a transform, a filter, `contain`, a container query...). Which properties
// do that differs between engines, so this asks the engine with a probe
// instead of keeping a list.
function fixedOrigin(host: HTMLElement): { x: number; y: number } {
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;left:0;top:0;width:0;height:0;visibility:hidden";
  host.appendChild(probe);
  const r = probe.getBoundingClientRect();
  probe.remove();
  return { x: r.left, y: r.top };
}

// The box the surface must stay inside. With the viewport as its containing
// block, a fixed surface escapes every ancestor's overflow, so the viewport is
// the whole box. Under a containing-block ancestor it does not, and this falls
// back to every ancestor that clips, each axis asked separately (see
// `narrowClip`).
function clipBox(el: HTMLElement, contained: boolean): Rect {
  const viewport = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
  if (!contained) return viewport;
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
  return narrowClip(viewport, clippers);
}

/**
 * Where an anchored surface should open, re-measured while it is open.
 *
 * @param open whether the surface is mounted. Closed, the placement resets to
 *   the caller's preference, so the next open never inherits the last flip.
 * @param side the PREFERRED side. A preference, not a guarantee.
 * @param anchorRef the positioned wrapper holding the trigger.
 * @param surfaceRef the surface itself, mounted only while open.
 * @returns `left` and `top` for the surface's fixed position, and the side
 *   it opened on.
 */
export function useAnchoredPlacement(
  open: boolean,
  side: "bottom" | "top",
  anchorRef: RefObject<HTMLElement | null>,
  surfaceRef: RefObject<HTMLElement | null>,
): { left: number; top: number; side: "bottom" | "top" } {
  const [at, setAt] = useState({ left: 0, top: 0 });
  // Where it ACTUALLY opened, which is `side` unless that would be clipped.
  const [placed, setPlaced] = useState<"bottom" | "top">(side);

  // Measure BEFORE paint, so a flip is never visible as a jump. Re-measures on
  // resize and on any ancestor scroll while open: the trigger moves under a
  // fixed surface without a close and reopen.
  useLayoutEffect(() => {
    if (!open) { setAt({ left: 0, top: 0 }); setPlaced(side); return; }
    const measure = () => {
      const surface = surfaceRef.current;
      const anchor = anchorRef.current;
      if (!surface || !anchor) return;
      const origin = fixedOrigin(anchor);
      const trigger = anchor.getBoundingClientRect();
      // Projected from the trigger and the surface's own size, never read
      // back from where the surface sits now (see menuPlacement.ts).
      const p = placeMenu(
        { left: trigger.left, top: trigger.top, right: trigger.right, bottom: trigger.bottom },
        { width: surface.offsetWidth, height: surface.offsetHeight },
        clipBox(anchor, origin.x !== 0 || origin.y !== 0),
        side,
        // The real gap the CSS leaves between trigger and surface, read from
        // the token rather than restated as a number that can drift from it.
        parseFloat(getComputedStyle(surface).getPropertyValue("--space-1")) || 4,
      );
      setAt({ left: trigger.left + p.insetStart - origin.x, top: p.top - origin.y });
      setPlaced(p.side);
    };
    measure();
    window.addEventListener("resize", measure);
    // Capture, because a scroll on an inner scroller does not bubble.
    window.addEventListener("scroll", measure, true);
    // A surface that fills in after it opens (rows fetched on open) is placed
    // at its first, shorter size: pinned by its top edge it then grows past
    // the clip box, and one opening upward grows down over its trigger.
    const grown = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (surfaceRef.current) grown?.observe(surfaceRef.current);
    return () => {
      grown?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, side, anchorRef, surfaceRef]);

  return { ...at, side: placed };
}
