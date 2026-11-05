// On a touch screen the dictation bar and the read-aloud player sit at the TOP
// of what is visible. The bottom is where the on-screen keyboard comes up, and
// how far up it reaches depends on the web view; the top of the visual
// viewport is visible whatever the keyboard does.

import { useEffect, useState } from "react";

const GAP = 8;

/** The dock's top edge in px on a touch screen, following the visible area;
 *  null elsewhere, where it stays at the bottom. */
export function useDockTop(touch: boolean): number | null {
  const [top, setTop] = useState<number | null>(null);
  useEffect(() => {
    if (!touch) { setTop(null); return; }
    const vv = window.visualViewport;
    const read = () => setTop(Math.round((vv?.offsetTop ?? 0) + GAP));
    read();
    vv?.addEventListener("resize", read);
    vv?.addEventListener("scroll", read);
    return () => {
      vv?.removeEventListener("resize", read);
      vv?.removeEventListener("scroll", read);
    };
  }, [touch]);
  return top;
}
