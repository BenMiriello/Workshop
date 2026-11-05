// A native shell draws page views UNDER its web UI, and the web UI is
// see-through: a page shows wherever nothing is drawn over its place, and
// everything the web UI draws there (a menu, a bar, a dialog, its shadow and
// round corners) shows over the page as the engine renders it. What would
// still hide a page is a background behind it: its slot's own, and those of
// every box that holds the slot, up to the document's root. While a page is
// shown, those backgrounds are taken away (the shell paints its own color
// natively behind everything), and an app in a frame tells the shell, which
// does the same for the frame and what holds it.

import { useEffect, type RefObject } from "react";

/** Posted by an app's frame to the shell: its page slot shows a page view. */
export const PAGE_SLOT_MSG = "workshop/v1/pageSlot";

const held = new WeakMap<HTMLElement, { count: number; value: string; priority: string }>();

/** Makes `el` and every element holding it in its document draw no
 *  background, until the returned function is called. Holds nest. */
export function seeThrough(el: Element): () => void {
  const chain: HTMLElement[] = [];
  for (let at: Element | null = el; at; at = at.parentElement) {
    if ("style" in at) chain.push(at as HTMLElement);
  }
  for (const box of chain) {
    const was = held.get(box);
    if (was) {
      was.count += 1;
      continue;
    }
    held.set(box, {
      count: 1,
      value: box.style.getPropertyValue("background"),
      priority: box.style.getPropertyPriority("background"),
    });
    box.style.setProperty("background", "transparent", "important");
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    for (const box of chain) {
      const was = held.get(box);
      if (!was || --was.count > 0) continue;
      held.delete(box);
      box.style.removeProperty("background");
      if (was.value) box.style.setProperty("background", was.value, was.priority);
    }
  };
}

function tellShell(shown: boolean): void {
  if (window.parent === window) return;
  window.parent.postMessage({ v: 1, type: PAGE_SLOT_MSG, shown }, window.location.origin);
}

/** While `shown`, the page view placed over `slot` is seen through this
 *  document and, in a frame, through the shell holding it. */
export function usePageSlot(slot: RefObject<HTMLElement>, shown: boolean): void {
  useEffect(() => {
    const el = slot.current;
    if (!shown || !el) return;
    const release = seeThrough(el);
    tellShell(true);
    return () => {
      release();
      tellShell(false);
    };
  }, [slot, shown]);
}
