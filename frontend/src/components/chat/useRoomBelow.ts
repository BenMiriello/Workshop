import { useCallback, useEffect, useRef, useState } from "react";

// How tall a list opening under a trigger may be: what is left below it, inside
// the page and inside any ancestor that clips (a drawer or sheet cuts the list
// off at its own edge, which is shorter than the page on a phone), less a margin
// so the list never runs to the very bottom.

const PAGE_BOTTOM_ROOM = 24;
// The list's own padding and border, which sit outside its scrolling body.
const LIST_CHROME = 18;
const MIN_ROOM = 160;

export function useRoomBelow<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [room, setRoom] = useState<number | null>(null);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    let bottom = window.innerHeight;
    for (let n = el.parentElement; n; n = n.parentElement) {
      if (getComputedStyle(n).overflowY !== "visible") {
        bottom = Math.min(bottom, n.getBoundingClientRect().bottom);
      }
    }
    const below = bottom - el.getBoundingClientRect().bottom;
    setRoom(Math.max(MIN_ROOM, below - LIST_CHROME - PAGE_BOTTOM_ROOM));
  }, []);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  return { ref, room, measure };
}
