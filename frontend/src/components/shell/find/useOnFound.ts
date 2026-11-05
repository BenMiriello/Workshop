// Opening what hides a match. Find fires the platform's own `beforematch`
// event at a match it is about to show when that match sits in content kept
// `hidden="until-found"` or clipped out of sight, and the event bubbles. A
// component that hides text (a collapsed disclosure, a clamped message) opens
// on it, the same way the browser's own find opens `until-found` content.

import { useEffect, useRef } from "react";

export const FOUND_EVENT = "beforematch";

/** Call `onFound` when a match inside `el` is about to be shown. */
export function useOnFound(el: HTMLElement | null, onFound: (() => void) | undefined): void {
  const latest = useRef(onFound);
  latest.current = onFound;
  useEffect(() => {
    if (!el) return;
    const found = () => latest.current?.();
    el.addEventListener(FOUND_EVENT, found);
    return () => el.removeEventListener(FOUND_EVENT, found);
  }, [el]);
}
