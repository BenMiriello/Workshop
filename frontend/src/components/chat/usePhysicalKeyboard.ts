import { useCallback, useEffect, useRef } from "react";
import { hasPhysicalKeyboard, isPhysicalKeyEvidence } from "./enterKey";

// Gathers the three signals `hasPhysicalKeyboard` decides from. The decision
// itself is pure and lives in enterKey.ts, with the reasoning; this file only
// reads the browser.
//
// Nothing here is state, on purpose. A re-render per keystroke to hold a
// boolean the composer reads once, on Enter, would be paid on every character
// typed — and the chat's typing path has already been tuned once for exactly
// that class of cost. Refs plus a read-on-demand call cost nothing.

function coarsePointer(): boolean {
  return typeof window !== "undefined"
    && !!window.matchMedia?.("(pointer: coarse)").matches;
}

/** How much of the window the on-screen keyboard is covering, or null if that
 *  cannot be told apart from something else. */
function viewportShrinkPx(): number | null {
  if (typeof window === "undefined") return null;
  const vv = window.visualViewport;
  if (!vv) return null;
  // Pinch zoom shrinks the visual viewport exactly like a keyboard does, so a
  // zoomed page cannot answer this question. Say so rather than read a zoom as
  // a keyboard.
  if (Math.abs(vv.scale - 1) > 0.01) return null;
  return Math.max(0, window.innerHeight - vv.height);
}

export interface PhysicalKeyboard {
  /** True if a physical keyboard is producing keystrokes right now. Call it at
   *  the moment you need it; it is a live read, not a snapshot. */
  check: () => boolean;
}

export function usePhysicalKeyboard(): PhysicalKeyboard {
  const saw = useRef(false);

  // Window-level and capture-phase so evidence counts wherever it happens: a
  // Tab through the toolbar or an Escape closing a menu proves the keyboard
  // just as well as an arrow key inside the textarea, and often sooner.
  useEffect(() => {
    if (saw.current) return;
    const onKey = (e: KeyboardEvent) => {
      if (isPhysicalKeyEvidence(e.key)) saw.current = true;
    };
    window.addEventListener("keydown", onKey, { capture: true, passive: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, []);

  const check = useCallback(() => hasPhysicalKeyboard({
    sawPhysicalKey: saw.current,
    coarsePointer: coarsePointer(),
    viewportShrinkPx: viewportShrinkPx(),
  }), []);

  return { check };
}
