// WHERE A CLAMPED BLOCK ACTUALLY ENDS.
//
// "Show N lines" was implemented as `-webkit-line-clamp: N` with
// `max-height: calc(N * 1lh)` under it as a floor for block content. Both are
// approximations and the second one is visibly wrong: N line heights is only N
// lines of text if every line is one line-height apart, and in markdown it is
// not. Paragraph margins, headings at a different size, a list, a code block,
// a table all shift the real line positions off that grid, so the cap lands
// mid-line and half a row of text shows above the fold. The operator has
// reported this more than once and it is the same complaint each time: it is
// not cut off, it is overflowing and being hidden.
//
// So the cut is MEASURED instead of computed. A Range over the block's contents
// reports one client rect per line box actually laid out, whatever produced it,
// and the bottom of the Nth of those is exactly where the block should end.
// Text, a heading, a table row and an image all report their real boxes, so the
// cut always lands on a boundary between two of them and never inside one.
//
// The DOM read lives in the component. This file is the arithmetic, so the part
// that can be wrong is the part that can be tested.

/** One bottom edge per LINE BOX, in document order.
 *
 *  A Range spanning inline content reports a rect per inline box, so a single
 *  line of text with a link and a bit of code in it arrives as three rects
 *  sharing a bottom. They are folded together. A taller box on the same line
 *  (an inline image) extends that line's bottom rather than starting a new one.
 *
 *  Rects that run BACKWARDS are ignored rather than reordered: a float can put
 *  one above the previous line, and it belongs to a line already counted.
 *  Sorting instead would silently merge two real lines into one. */
export function lineBottoms(
  rects: readonly { bottom: number; height: number }[],
): number[] {
  const out: number[] = [];
  for (const r of rects) {
    if (r.height <= 0) continue;
    if (!out.length) { out.push(r.bottom); continue; }
    const last = out[out.length - 1];
    // More than half a pixel below the last line: a new line box.
    if (r.bottom > last + 0.5) out.push(r.bottom);
    else if (r.bottom > last) out[out.length - 1] = r.bottom;
  }
  return out;
}

/** The exact height that shows `lines` whole line boxes and no part of the one
 *  after. `top` is the block's own border-box top, in the same coordinates as
 *  the rects. Null when there is nothing past the fold to cut.
 *
 *  Rounded UP, because a fractional line bottom rounded down shaves a hairline
 *  off the last row of text, which is the same defect one pixel smaller. */
export function cutHeight(
  bottoms: readonly number[], top: number, lines: number,
): number | null {
  if (lines < 1 || bottoms.length <= lines) return null;
  return Math.max(0, Math.ceil(bottoms[lines - 1] - top));
}

/** Is clamping worth doing at all?
 *
 *  Only once there are 2+ lines to hide. Clamping a block that is ONE line over
 *  hides one line and puts a Show more row where it was: no shorter, just
 *  different. */
export function worthClamping(total: number, lines: number): boolean {
  return total > lines + 1;
}
