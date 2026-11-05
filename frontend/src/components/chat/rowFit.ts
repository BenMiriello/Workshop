// HOW MUCH OF A ROW OF CONTROLS FITS, asked of the row itself.
//
// The composer's action row used to drop labels at a width of the WINDOW. Its
// own width is what matters: the chat beside a page can be a third of the
// window or less, and then the row ran off its right edge with every label
// still drawn. So the row is measured instead. Each level is a step in the
// stylesheet (`.chat-actions[data-fit="n"]`), cheapest loss first, and the
// level chosen is the first one at which nothing overflows.

import { useLayoutEffect, type RefObject } from "react";

export const FIT_LAST = 5;

/** The first level at which `overflows` is false, or the last when none is. */
export function pickFit(overflows: (level: number) => boolean, last = FIT_LAST): number {
  for (let level = 0; level < last; level++) {
    if (!overflows(level)) return level;
  }
  return last;
}

/** The chat's name is the one thing on the toolbar that shrinks, so a crowded
 *  toolbar never overflows: it squeezes the name instead. This says when the
 *  name has been squeezed too far to read, which is the cue to drop the labels
 *  beside it. A long name with room to spare is left alone. */
export const NAME_MIN_PX = 120;

export function nameSqueezed(row: HTMLElement): boolean {
  const name = row.querySelector<HTMLElement>(".chat-tb__title");
  return !!name && name.clientWidth < NAME_MIN_PX && name.scrollWidth > name.clientWidth + 1;
}

export function useRowFit(
  ref: RefObject<HTMLElement | null>,
  key: string,
  opts: { last?: number; squeezed?: (row: HTMLElement) => boolean } = {},
): void {
  const { last = FIT_LAST, squeezed } = opts;
  useLayoutEffect(() => {
    const row = ref.current;
    if (!row) return;
    const fit = () => {
      const level = pickFit((l) => {
        row.dataset.fit = String(l);
        return row.scrollWidth > row.clientWidth + 1 || !!squeezed?.(row);
      }, last);
      row.dataset.fit = String(level);
    };
    fit();
    // The row's width moving is a resize; a child changing size (a longer
    // model name, a renamed chat, the percentage gaining a digit) is the other
    // thing that can change the answer. The row answering its own level change
    // settles after one more pass, since it lands on the same level.
    const watch = new ResizeObserver(fit);
    watch.observe(row);
    for (const child of Array.from(row.children)) watch.observe(child);
    return () => watch.disconnect();
  }, [ref, key, last, squeezed]);
}
