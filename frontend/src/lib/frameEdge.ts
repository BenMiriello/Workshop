import { useEffect } from "react";

// WHETHER A FRAMED PAGE'S BOTTOM IS THE SCREEN'S BOTTOM.
//
// `env(safe-area-inset-bottom)` is the home bar's height in every frame on
// iOS, wherever the frame sits: measured 2026-10-25 on iOS 26.5 WebKit, 34px
// in the shell, in an app's frame and in a chat framed inside that app, whose
// bottom edge was the app's own nav bar 83px above the screen's edge. A chat
// embedded in an app therefore padded its composer for a home bar it was
// nowhere near, which is the dead row under its buttons.
//
// Only the page holding the frame knows where the frame ends, so the framed
// page asks: it walks its frames up to the top window (all one origin here)
// and compares its bottom with the bottom of what the top window shows. When
// the two differ, `--edge-inset-b` is 0 and layout that pads for the home bar
// reads that instead of the inset. A frame that does reach the bottom (a
// sheet over an app's page) keeps the inset.

/** The part of the visible window between a frame's bottom edge and the
 *  window's, in CSS px; 0 or less means the frame reaches the bottom. */
export function gapBelow(frameBottom: number, viewHeight: number): number {
  return Math.round(viewHeight - frameBottom);
}

/** Below this the frame counts as reaching the bottom: borders and rounding
 *  move an edge by a pixel or two. */
export const EDGE_SLOP_PX = 2;

/** Where this window's bottom edge sits in the top window's visible area,
 *  and that area's height. Null at the top level, or when a frame between
 *  here and the top belongs to another origin. */
export function bottomInTop(win: Window): { bottom: number; height: number } | null {
  if (win === win.top) return null;
  let bottom = win.innerHeight;
  let w: Window = win;
  try {
    while (w !== w.top) {
      const frame = w.frameElement;
      if (!frame) return null;
      bottom = Math.min(bottom, frame.clientHeight) + frame.getBoundingClientRect().top
        + frame.clientTop;
      w = w.parent;
    }
    return { bottom, height: w.visualViewport?.height ?? w.innerHeight };
  } catch {
    return null;
  }
}

/** Keep `--edge-inset-b` at 0 on this document while its frame ends above the
 *  bottom of the screen; unset (so the inset applies) otherwise. */
export function useFrameEdge(): void {
  useEffect(() => {
    const root = document.documentElement.style;
    const top = (() => { try { return window.top; } catch { return null; } })();
    const read = () => {
      const at = bottomInTop(window);
      if (at && gapBelow(at.bottom, at.height) > EDGE_SLOP_PX) root.setProperty("--edge-inset-b", "0px");
      else root.removeProperty("--edge-inset-b");
    };
    read();
    // The frame is moved by its host and by the keyboard, neither of which
    // this page sees as anything but a resize of its own or of the top.
    const timers = [120, 600].map((ms) => window.setTimeout(read, ms));
    window.addEventListener("resize", read);
    top?.addEventListener("resize", read);
    top?.visualViewport?.addEventListener("resize", read);
    return () => {
      timers.forEach(window.clearTimeout);
      window.removeEventListener("resize", read);
      top?.removeEventListener("resize", read);
      top?.visualViewport?.removeEventListener("resize", read);
      root.removeProperty("--edge-inset-b");
    };
  }, []);
}
