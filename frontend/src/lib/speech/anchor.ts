// What the dictation chip is placed against: the area the operator sees as
// the field, which is not always the element holding focus.
//
// Terminals and code editors (xterm, Monaco, Ace) take keystrokes through a
// hidden helper textarea, a few pixels wide and parked under the text cursor.
// Placing the chip on it puts the chip on the cursor. So the chip goes on the
// nearest enclosing area big enough to be what is on screen, unless the field
// itself is a visible size. An app can name the area outright by marking an
// element `data-dictation-anchor`; it never has to.

import type { Box } from "./placement";

export type AnchorCandidate = {
  box: Box;
  /** Carries `data-dictation-anchor`. */
  marked: boolean;
  /** Painted at all: not transparent, not visibility-hidden. */
  visible: boolean;
};

// A field smaller than this is a stand-in for something else on screen.
const FIELD_MIN_W = 48;
const FIELD_MIN_H = 18;
// An enclosing area this size can carry the chip in its corner.
const AREA_MIN_W = 120;
const AREA_MIN_H = 40;

const width = (b: Box) => b.right - b.left;
const height = (b: Box) => b.bottom - b.top;

/** Index into `chain` (the focused field first, then its ancestors outwards)
 *  of the element the chip is placed against. */
export function chooseAnchor(chain: AnchorCandidate[]): number {
  const marked = chain.findIndex((c) => c.marked);
  if (marked >= 0) return marked;
  const field = chain[0];
  if (!field) return 0;
  if (field.visible && width(field.box) >= FIELD_MIN_W && height(field.box) >= FIELD_MIN_H) return 0;
  const area = chain.findIndex((c, i) => i > 0 && c.visible
    && width(c.box) >= AREA_MIN_W && height(c.box) >= AREA_MIN_H);
  return area > 0 ? area : 0;
}

/** The part of `box` inside `view`, so a field taller than the screen still
 *  gets its chip in the corner that can be seen. Null when none of it is. */
export function visiblePart(box: Box, view: Box): Box | null {
  const out = {
    left: Math.max(box.left, view.left),
    top: Math.max(box.top, view.top),
    right: Math.min(box.right, view.right),
    bottom: Math.min(box.bottom, view.bottom),
  };
  return out.right > out.left && out.bottom > out.top ? out : null;
}
