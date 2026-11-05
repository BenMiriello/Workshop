// Keeping a tapped line where it was while reading, as the editor takes over.
//
// The editor draws only the lines near the screen and ESTIMATES the height of
// the rest, then replaces estimates with measurements as lines are drawn. A
// document scrolled by an outer box (a note pane, a page) is laid out against
// those estimates the moment editing starts, so the tapped line moved, and
// moved again as each guess was corrected: measured 2026-10-24 on a long list
// note, 445px while reading, 414 on the first frame, 364 once settled.
//
// So the line is held at the height it was read at: corrected in the editor's
// own measure cycle (before paint) on every update, until the reader scrolls,
// touches or types, or a second and a half has passed.
import type { EditorView, ViewUpdate } from "@codemirror/view";

const PIN_MS = 1500;
const STOP_ON = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

function scrollParentOf(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const y = getComputedStyle(p).overflowY;
    if ((y === "auto" || y === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

export interface LinePin {
  onUpdate(u: ViewUpdate): void;
  stop(): void;
}

/** Hold the line holding `pos` at viewport y `top` (the top of its text). */
export function pinLine(view: EditorView, pos: number, top: number): LinePin {
  const scroller = scrollParentOf(view.dom);
  let live = scroller != null;
  const key = {};
  const textTop = (v: EditorView) => v.coordsAtPos(pos)?.top ?? v.documentTop + v.lineBlockAt(pos).top;
  const move = (y: number) => {
    if (live && scroller && Math.abs(y - top) > 0.5) scroller.scrollTop += y - top;
  };
  const stop = () => {
    live = false;
    clearTimeout(timer);
    for (const e of STOP_ON) scroller?.removeEventListener(e, stop);
  };
  const timer = setTimeout(stop, PIN_MS);
  for (const e of STOP_ON) scroller?.addEventListener(e, stop, { passive: true });
  // Settled before the first paint: `coordsAtPos` runs a requested measure at
  // once, so each pass draws and measures the lines where the last one
  // scrolled to. WebKit painted one frame 180px off when this waited for the
  // editor's own measure cycle.
  for (let i = 0; live && i < 4; i++) {
    view.requestMeasure();
    const y = textTop(view);
    if (Math.abs(y - top) <= 0.5) break;
    move(y);
  }
  return {
    onUpdate(u) {
      if (!live) return;
      if (u.docChanged) { stop(); return; }
      if (u.viewportChanged || u.heightChanged || u.geometryChanged) {
        u.view.requestMeasure({ key, read: textTop, write: move });
      }
    },
    stop,
  };
}
