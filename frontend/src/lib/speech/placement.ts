// Where the dictation chip sits, as arithmetic over rectangles in the shell
// window's coordinates. The chip is an overlay drawn by the shell, so it never
// shifts a field's layout, and it may sit over a field inside an app frame.
//
// In a text field it sits on the row of the field's LAST LINE of text,
// centred on that line, at the right end, beside any button of the field's own
// (rowSpot). It never covers the field's text and never changes the field:
// when that line's text already reaches the chip, it takes the empty row
// below, or just outside the field's right edge, and when neither is free the
// field has no chip (the shortcut and the bar still dictate into it). An empty
// field's placeholder is kept clear when there is room, and covered when not.
//
// An area standing in for a hidden helper (a terminal) has no text line of its
// own, so the chip goes in the first free spot along its bottom edge
// (freeSpot). Placing it against the on-screen keyboard instead depended on
// how each web view reports the keyboard, and in the iPhone app it ended up
// behind it.

export type Box = { left: number; top: number; right: number; bottom: number };

/** The visible window: the visual viewport on a phone, the window elsewhere. */
export type Viewport = { left: number; top: number; width: number; height: number };

export type Spot = { left: number; top: number };

/** The chip's size, mirrored by `.ws-speech-chip` and its touch modifier. */
export const CHIP_SIZE = 24;
export const CHIP_SIZE_TOUCH = 32;

const INSET = 4;
const MARGIN = 8;

/** The chip inside a field's bottom-right corner. A field shorter than the
 *  chip (a compact input) gets it centred on its height instead. Null when
 *  the corner is outside the viewport, so a field scrolled away loses it. */
export function cornerSpot(field: Box, size: number, view: Viewport): Spot | null {
  const height = field.bottom - field.top;
  const top = height < size + INSET * 2
    ? field.top + (height - size) / 2
    : field.bottom - size - INSET;
  const left = field.right - size - INSET;
  const inside = left >= view.left && top >= view.top
    && left + size <= view.left + view.width && top + size <= view.top + view.height;
  return inside ? { left, top } : null;
}

/** The first spot inside `field` where the chip covers nothing `blocked`
 *  reports, scanning leftwards along the bottom edge and then the top edge
 *  (one row, centred, for a field barely taller than the chip). When every
 *  spot inside is taken, just outside the field's right edge, then the corner.
 *  Null when the field is off screen. */
export function freeSpot(
  field: Box, size: number, view: Viewport, blocked: (chip: Box) => boolean,
): Spot | null {
  const corner = cornerSpot(field, size, view);
  if (!corner) return null;
  const height = field.bottom - field.top;
  const rows = height < size * 2 + INSET * 3 ? [corner.top] : [corner.top, field.top + INSET];
  const step = 8;
  const fits = (left: number, top: number) => left >= view.left && top >= view.top
    && left + size <= view.left + view.width && top + size <= view.top + view.height;
  const free = (left: number, top: number) =>
    fits(left, top) && !blocked({ left, top, right: left + size, bottom: top + size });
  for (const top of rows) {
    for (let left = corner.left; left >= field.left + INSET; left -= step) {
      if (free(left, top)) return { left, top };
    }
  }
  const outside = field.right + INSET;
  if (free(outside, corner.top)) return { left: outside, top: corner.top };
  return corner;
}

/** Where a text field's text ends, in the shell window's coordinates. */
export type TextEnd = {
  /** Just past the last character, on its line. */
  x: number;
  /** The vertical centre of that line, and the height of a line. */
  rowCenter: number;
  lineHeight: number;
  /** The field's inside right edge: inside the border, left of any scrollbar. */
  innerRight: number;
  /** The text measured is the placeholder of an empty field. */
  hint?: boolean;
  /** The line the caret is on: just past its last character, and its centre. */
  caret?: { x: number; rowCenter: number } | null;
  /** Other lines in sight, nearest the caret first, the same way. */
  near?: { x: number; rowCenter: number }[];
};

const GAP = 4;
const STEP = 2;

/** The chip on the row of the field's last line (or, with that line out of
 *  sight, the caret's), at its right end, stepping left past the page's own
 *  controls. When the text on that line reaches the
 *  chip: the empty row below, if the field has one; else just outside the
 *  field's right edge; else, when that text is only a placeholder, over its
 *  end. Null when none of those is free, or the field is off screen. `field`
 *  is the part of the field that can be seen. */
export function rowSpot(
  field: Box, end: TextEnd, size: number, view: Viewport, blocked: (chip: Box) => boolean,
): Spot | null {
  if (field.right <= field.left || field.bottom <= field.top) return null;
  const topIn = (center: number) => (field.bottom - field.top < size + 2
    ? field.top + (field.bottom - field.top - size) / 2
    : Math.min(Math.max(center - size / 2, field.top + 1), field.bottom - size - 1));
  const free = (left: number, top: number) => left >= view.left && left + size <= view.left + view.width
    && top >= view.top && top + size <= view.top + view.height
    && !blocked({ left, top, right: left + size, bottom: top + size });
  const onRow = (center: number): Spot | null => {
    const top = topIn(center);
    for (let left = Math.min(end.innerRight, field.right) - INSET - size; left >= field.left + INSET; left -= STEP) {
      if (free(left, top)) return { left, top };
    }
    return null;
  };
  // A last line scrolled out of sight has no row here: held to the visible
  // part, the chip would land on some other line's text. The line the caret
  // is on stands in for it, at its end or just outside the field.
  const lineShown = end.rowCenter >= field.top && end.rowCenter <= field.bottom;
  const caret = !lineShown && end.caret && end.caret.rowCenter >= field.top && end.caret.rowCenter <= field.bottom
    ? end.caret : null;
  if (caret) {
    const at = onRow(caret.rowCenter);
    if (at && at.left >= caret.x + GAP) return at;
    const out = { left: field.right + INSET, top: topIn(caret.rowCenter) };
    if (free(out.left, out.top)) return out;
    // A line nearby with room at its end: a paragraph's short last line, or
    // a blank one.
    for (const row of end.near ?? []) {
      if (row.rowCenter - size / 2 < field.top || row.rowCenter + size / 2 > field.bottom) continue;
      const spot = onRow(row.rowCenter);
      if (spot && spot.left >= row.x + GAP) return spot;
    }
  }
  const line = lineShown ? onRow(end.rowCenter) : null;
  if (line && line.left >= end.x + GAP) return line;
  const next = end.rowCenter + end.lineHeight;
  if (next + size / 2 <= field.bottom - 1) {
    const below = onRow(next);
    if (below) return below;
  }
  const outside = { left: field.right + INSET, top: topIn(end.rowCenter) };
  if (free(outside.left, outside.top)) return outside;
  // A placeholder goes at the first key typed, so it does not keep the chip
  // off an empty field; typed text does.
  return end.hint ? line : null;
}

/** A control placed just below a selection, centred on it, held inside the
 *  viewport. When there is no room below, it goes above instead. */
export function belowSpot(sel: Box, width: number, height: number, view: Viewport): Spot {
  const centre = (sel.left + sel.right) / 2;
  const minLeft = view.left + MARGIN;
  const maxLeft = view.left + view.width - width - MARGIN;
  const left = Math.min(Math.max(centre - width / 2, minLeft), Math.max(minLeft, maxLeft));
  let top = sel.bottom + MARGIN * 1.5;
  if (top + height > view.top + view.height - MARGIN) top = sel.top - height - MARGIN;
  return { left, top: Math.max(view.top + MARGIN, top) };
}
