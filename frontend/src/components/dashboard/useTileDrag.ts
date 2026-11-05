import { useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { move, pressIsTap } from "./arrange";

const DRAG_PX = 10;
const SETTLE_MS = 160;

type Held = {
  id: string; index: number; pointer: number; at: number; x: number; y: number; moved: boolean;
  slots: DOMRect[]; cur: number; order: string[]; grab: { x: number; y: number };
};

function slotAt(slots: DOMRect[], x: number, y: number): number {
  let best = 0;
  let bestD = Infinity;
  slots.forEach((r, i) => {
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) { best = i; bestD = -1; }
    else if (bestD >= 0) {
      const d = Math.hypot(x - (r.left + r.right) / 2, y - (r.top + r.bottom) / 2);
      if (d < bestD) { best = i; bestD = d; }
    }
  });
  return best;
}

/** Drag-to-reorder for a grid of tiles. A press that barely moves is a tap. Past
 *  that the tile follows the pointer, the others make room as it passes over
 *  their places, and letting go drops it in the place it is over.
 *
 *  No tap is lost to fast tapping: a second finger landing while the first is
 *  still down counts the first at once (the order fingers landed in is the
 *  order tapped), and a quick press that wobbled but ended on its own tile is
 *  still a tap (`pressIsTap`). */
export function useTileDrag(
  ids: string[],
  onTap: ((index: number) => void) | undefined,
  onDrop: (order: string[]) => void,
) {
  const listRef = useRef<HTMLUListElement>(null);
  const held = useRef<Held | null>(null);
  const rects = useRef(new Map<string, DOMRect>());
  const [live, setLive] = useState<string[] | null>(null);
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number; settling: boolean } | null>(null);
  const order = live ?? ids;

  // Tiles that changed place slide there instead of jumping.
  useLayoutEffect(() => {
    const next = new Map<string, DOMRect>();
    listRef.current?.querySelectorAll<HTMLElement>("[data-tile-id]").forEach((el) => {
      const id = el.dataset.tileId ?? "";
      const r = (el.parentElement as HTMLElement).getBoundingClientRect();
      next.set(id, r);
      const was = rects.current.get(id);
      if (!was || id === held.current?.id || (was.left === r.left && was.top === r.top)) return;
      el.style.transition = "none";
      el.style.transform = `translate(${was.left - r.left}px, ${was.top - r.top}px)`;
      el.getBoundingClientRect();
      el.style.transition = `transform ${SETTLE_MS}ms ease`;
      el.style.transform = "";
      el.addEventListener("transitionend", () => { el.style.transition = ""; }, { once: true });
    });
    rects.current = next;
  });

  const finish = (dropped: string[] | null) => {
    const h = held.current;
    held.current = null;
    if (h?.moved) {
      setDrag({ id: h.id, dx: 0, dy: 0, settling: true });
      window.setTimeout(() => setDrag(null), SETTLE_MS);
    } else setDrag(null);
    setLive(null);
    if (dropped) onDrop(dropped);
  };

  const props = (id: string, index: number) => ({
    "data-tile-id": id,
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const was = held.current;
      if (was?.moved) return;
      if (was) { held.current = null; onTap?.(was.index); }
      const slot = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
      const slots = Array.from(listRef.current?.children ?? []).map((c) => c.getBoundingClientRect());
      held.current = {
        id, index, pointer: e.pointerId, at: e.timeStamp,
        x: e.clientX, y: e.clientY, moved: false, slots, cur: index, order: ids,
        grab: { x: e.clientX - (slot.left + slot.right) / 2, y: e.clientY - (slot.top + slot.bottom) / 2 },
      };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: PointerEvent<HTMLElement>) => {
      const h = held.current;
      if (!h || h.pointer !== e.pointerId) return;
      if (!h.moved && Math.hypot(e.clientX - h.x, e.clientY - h.y) < DRAG_PX) return;
      h.moved = true;
      const to = slotAt(h.slots, e.clientX, e.clientY);
      if (to !== h.cur) {
        h.order = move(h.order, h.cur, to);
        h.cur = to;
        setLive(h.order);
      }
      const s = h.slots[h.cur];
      setDrag({
        id, settling: false,
        dx: e.clientX - h.grab.x - (s.left + s.right) / 2,
        dy: e.clientY - h.grab.y - (s.top + s.bottom) / 2,
      });
    },
    onPointerUp: (e: PointerEvent<HTMLElement>) => {
      const h = held.current;
      if (!h || h.pointer !== e.pointerId) return;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
      const home = h.cur === h.index;
      if (pressIsTap(h.moved, home, e.timeStamp - h.at)) { finish(null); onTap?.(h.index); return; }
      finish(home ? null : h.order);
    },
    onPointerCancel: (e: PointerEvent<HTMLElement>) => {
      if (held.current?.pointer === e.pointerId) finish(null);
    },
  });

  const style = (id: string): CSSProperties | undefined =>
    drag?.id === id ? ({ "--dx": `${drag.dx}px`, "--dy": `${drag.dy}px` } as CSSProperties) : undefined;

  return { listRef, order, dragging: drag?.id ?? null, settling: drag?.settling ?? false, props, style };
}
