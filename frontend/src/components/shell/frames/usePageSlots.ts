// The shown app's frame, see-through while a page view shows in it.
//
// A native shell draws page views under this document, so a page shows only
// where nothing here paints over it. An app with a page in its slot says so
// (`workshop/v1/pageSlot`, the kit's `usePageSlot`), and while that frame is
// the shown one, it and every box holding it draw no background; the shell
// paints the theme's color natively behind the whole document.

import { useEffect, useState } from "react";
import { PAGE_SLOT_MSG, seeThrough } from "@workshop/ui";

export function usePageSlots(active: string | null, elements: Map<string, HTMLIFrameElement>): void {
  const [showing, setShowing] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    const said = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.data?.type !== PAGE_SLOT_MSG) return;
      for (const [key, el] of elements) {
        if (el.contentWindow !== e.source) continue;
        const shown = e.data.shown === true;
        setShowing((was) => {
          if (was.has(key) === shown) return was;
          const next = new Set(was);
          if (shown) next.add(key);
          else next.delete(key);
          return next;
        });
      }
    };
    window.addEventListener("message", said);
    return () => window.removeEventListener("message", said);
  }, [elements]);

  const el = active && showing.has(active) ? elements.get(active) : undefined;
  useEffect(() => (el ? seeThrough(el) : undefined), [el]);
}
