// The Apps card's arranged order (backend/services/dashboard_order.py), and
// when a press on a tile is a tap rather than a drag.

/** `items` in the saved order. Ids the order does not name keep their own
 *  relative order after the named ones; named ids that are absent are skipped. */
export function orderBy<T>(items: readonly T[], order: readonly string[], id: (t: T) => string): T[] {
  const rank = new Map(order.map((k, i) => [k, i]));
  const named = items.filter((t) => rank.has(id(t)))
    .sort((a, b) => (rank.get(id(a)) ?? 0) - (rank.get(id(b)) ?? 0));
  return [...named, ...items.filter((t) => !rank.has(id(t)))];
}

/** `list` with the item at `from` moved to `to`. */
export function move<T>(list: readonly T[], from: number, to: number): T[] {
  const out = [...list];
  if (from === to || from < 0 || to < 0 || from >= out.length || to >= out.length) return out;
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

/** How long a press may last and still be a tap after it wobbled past the drag
 *  threshold. A finger typing fast slides further than one pressing slowly. */
export const TAP_MS = 300;

/** Whether a press on a tile was a tap: it never moved far enough to drag, or
 *  it was quick and let go over its own place. */
export function pressIsTap(moved: boolean, endedHome: boolean, heldMs: number): boolean {
  return !moved || (endedHome && heldMs < TAP_MS);
}
