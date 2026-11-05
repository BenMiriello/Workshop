// Placing the shell's overlays (the mic chip, the destination ring) over a
// field: where the visible area is, and where `position: fixed` space starts.
//
// TWO COORDINATE SPACES, which are the same until a phone's keyboard is up.
// Element boxes (getBoundingClientRect) move with the document's scroll;
// `position: fixed`, which the overlays are drawn with, and the visual
// viewport's offset do not. On iOS the document scrolls under the pinned shell
// when the keyboard opens (lib/useVisualViewport), so the two part by that
// scroll. Measured 2026-10-25 on iOS 26.5 WebKit: page scrolled 346px, a fixed
// element at top 100 boxed at -246. Placing the chip by a field's box drew it
// 346px above the field, and judging the box against the viewport's offset
// read the field as off screen, so it got no chip at all. A probe at fixed 0,0
// says where fixed space starts in box space, on any engine.

import type { Box } from "../../../lib/speech/placement";

export function viewport() {
  const vv = window.visualViewport;
  return vv
    ? { left: vv.offsetLeft, top: vv.offsetTop, width: vv.width, height: vv.height }
    : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
}

let probe: HTMLElement | null = null;
export function fixedOrigin(): { left: number; top: number } {
  if (!probe || !probe.isConnected) {
    probe = document.createElement("div");
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = "position:fixed;left:0;top:0;width:0;height:0;visibility:hidden;pointer-events:none";
    document.body.appendChild(probe);
  }
  const r = probe.getBoundingClientRect();
  return { left: r.left, top: r.top };
}

/** The visible area, in box space. */
export function viewBox(): { left: number; top: number; width: number; height: number } {
  const o = fixedOrigin();
  const v = viewport();
  return { ...v, left: v.left + o.left, top: v.top + o.top };
}

export const sameBox = (a: Box, b: Box | null) => !!b && Math.round(a.left) === Math.round(b.left)
  && Math.round(a.top) === Math.round(b.top) && Math.round(a.right) === Math.round(b.right)
  && Math.round(a.bottom) === Math.round(b.bottom);
