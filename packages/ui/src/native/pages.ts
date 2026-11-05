// Web-side helpers for native page views (the `pages` capability of the
// Workshop mobile shell; contract in shared/native-pages.ts).
//
// The shell installs its bridge on the top Workshop document only. An app the
// Workshop SPA frames on the same origin reaches it through `window.parent`,
// so `pagesBridge()` looks there too, and `topViewportRect()` converts an
// element's rect into the top document's viewport, which is the space page
// view rects are given in.
//
// The shell keeps each app's page views and their data apart by the app's id
// (shared/native-pages.ts), so `pagesBridge()` hands back the bridge acting
// for the app this frame serves (./appBridge.ts). A shell from before page
// views were per app has no `forApp`; its pages are shared, as they always were.

import { forFrameApp, reachBridge } from "./appBridge";
import type { PageRect, WorkshopNative } from "./types";

export { frameApp } from "./appBridge";

/**
 * The bridge that can drive page views, from this window or a same-origin
 * ancestor; undefined in a plain browser or a shell without page views.
 * Subscribe through the returned object's `on`, so events and calls share it.
 */
export function pagesBridge(): WorkshopNative | undefined {
  const found = reachBridge("pages");
  if (!found) return undefined;
  const bridge = forFrameApp(found.bridge) ?? found.bridge;
  return found.framed ? closingWithFrame(bridge) : bridge;
}

const closers = new WeakSet<Window>();

/** Page views are native and outlive any frame, so when the framed app that opened
 *  them goes away (the SPA navigates elsewhere and drops its frame), they
 *  must go too, or they stay open behind whatever comes next. `bridge`
 *  acts for this frame's app, so only that app's pages close. */
function closingWithFrame(bridge: WorkshopNative): WorkshopNative {
  if (!closers.has(window)) {
    closers.add(window);
    const close = () => { void bridge.pages.closeAll(); };
    window.addEventListener("pagehide", close);
    window.addEventListener("unload", close);
  }
  return bridge;
}

/** `el`'s rect in CSS pixels of the top same-origin document's viewport. */
export function topViewportRect(el: Element): PageRect {
  const r = el.getBoundingClientRect();
  let x = r.left;
  let y = r.top;
  try {
    let win: Window | null = el.ownerDocument.defaultView;
    while (win && win.parent !== win && win.frameElement) {
      const frame = win.frameElement as HTMLElement;
      const f = frame.getBoundingClientRect();
      x += f.left + frame.clientLeft;
      y += f.top + frame.clientTop;
      win = win.parent;
    }
  } catch {
    // Stop at a cross-origin ancestor.
  }
  return { x, y, width: r.width, height: r.height };
}
