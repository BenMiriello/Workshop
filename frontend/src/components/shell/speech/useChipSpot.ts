// Where the mic chip sits on a field, followed every frame while shown: a
// field moves with scrolling, layout and the keyboard, and none of those
// announce themselves to an overlay. Where the page's own controls sit is asked
// again only when the field has moved, or now and then for a control that
// appeared on hover.

import { useEffect, useState } from "react";
import { visiblePart } from "../../../lib/speech/anchor";
import {
  CHIP_SIZE, CHIP_SIZE_TOUCH, freeSpot, rowSpot, type Box, type Spot,
} from "../../../lib/speech/placement";
import { textEnd } from "./fieldRow";
import { fixedOrigin, sameBox, viewBox } from "./fixedSpace";
import { anchorOf, coversControl, visibleBox } from "./frames";

const RECHECK_MS = 500;

const sameSpot = (a: Spot | null, b: Spot | null) =>
  a === b || (!!a && !!b && Math.round(a.left) === Math.round(b.left) && Math.round(a.top) === Math.round(b.top));

// In a text field the chip goes on the row of its last line of text, at the
// right end, without changing the field (placement.ts has the rule). An area
// standing in for a hidden helper gets the first free spot on its bottom edge.
function chipSpot(field: HTMLElement, anchor: { el: Element; box: Box }, touch: boolean): Spot | null {
  const o = fixedOrigin();
  const view = viewBox();
  const shown = visibleBox(anchor.el, anchor.box);
  const seen = shown && visiblePart(shown, {
    left: view.left, top: view.top, right: view.left + view.width, bottom: view.top + view.height,
  });
  if (!seen) return null;
  const size = touch ? CHIP_SIZE_TOUCH : CHIP_SIZE;
  const blocked = (chip: Box) => coversControl(field, chip);
  const end = anchor.el === field ? textEnd(field, anchor.box) : null;
  const spot = end ? rowSpot(seen, end, size, view, blocked) : freeSpot(seen, size, view, blocked);
  // On whole pixels, so the ring and the icon inside it round alike.
  return spot && { left: Math.round(spot.left - o.left), top: Math.round(spot.top - o.top) };
}

export function useChipSpot(anchor: HTMLElement | null, touch: boolean): Spot | null {
  const [spot, setSpot] = useState<Spot | null>(null);
  useEffect(() => {
    if (!anchor) { setSpot(null); return; }
    let raf = 0;
    let last: Box | null = null;
    let checked = 0;
    // Typing and scrolling inside the field move where its text ends.
    let edited = false;
    const edit = () => { edited = true; };
    anchor.addEventListener("input", edit);
    anchor.addEventListener("scroll", edit);
    const tick = () => {
      const at = anchorOf(anchor);
      const now = performance.now();
      if (!anchor.isConnected) setSpot(null);
      else if (edited || !sameBox(at.box, last) || now - checked > RECHECK_MS) {
        edited = false;
        last = at.box;
        checked = now;
        const next = chipSpot(anchor, at, touch);
        setSpot((prev) => (sameSpot(prev, next) ? prev : next));
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      cancelAnimationFrame(raf);
      anchor.removeEventListener("input", edit);
      anchor.removeEventListener("scroll", edit);
    };
  }, [anchor, touch]);
  return spot;
}
