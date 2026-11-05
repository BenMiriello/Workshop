// Where the dictation bar sits once the operator has dragged it: anywhere on
// screen, kept inside it. Near its own spot it is held there on an elastic:
// it leans toward the pointer a little, and past one distance it breaks away
// and follows; dragged back within that distance it is pulled in again. Let go
// while held, it is home.

export type Point = { left: number; top: number };
export type Size = { width: number; height: number };

/** Within this distance of its own spot, the bar is held there. */
export const SNAP_PX = 56;
/** How far past SNAP_PX a held bar may be dragged before it breaks away, so it
 *  does not flicker in and out at the edge. */
const EDGE_PX = 6;
/** How much of the pointer's pull a held bar shows. */
export const STRETCH = 0.3;
const MARGIN = 8;

export function clampBar(p: Point, size: Size, view: Size): Point {
  const maxLeft = Math.max(MARGIN, view.width - size.width - MARGIN);
  const maxTop = Math.max(MARGIN, view.height - size.height - MARGIN);
  return {
    left: Math.min(Math.max(p.left, MARGIN), maxLeft),
    top: Math.min(Math.max(p.top, MARGIN), maxTop),
  };
}

/** Where a bar dragged to `raw` shows, and whether it is held at its own spot
 *  (`home`); `pulled` is whether it was a move ago. */
export function dragBar(
  raw: Point, home: Point, pulled: boolean, size: Size, view: Size,
): { at: Point; pulled: boolean } {
  const dx = raw.left - home.left;
  const dy = raw.top - home.top;
  const near = Math.hypot(dx, dy) <= SNAP_PX + (pulled ? EDGE_PX : 0);
  if (!near) return { at: clampBar(raw, size, view), pulled: false };
  return { at: { left: home.left + dx * STRETCH, top: home.top + dy * STRETCH }, pulled: true };
}

export function parsePoint(raw: string | null): Point | null {
  try {
    const v = JSON.parse(raw ?? "null");
    return v && Number.isFinite(v.left) && Number.isFinite(v.top) ? { left: v.left, top: v.top } : null;
  } catch {
    return null;
  }
}
