// What the shell draws over the shown app's frame, told to a native shell.
//
// A native shell draws page views (an app's web pages) under the web UI, so
// anything the shell puts over an app (the dictation bar, a notice, a menu,
// the chat drawer on a phone) shows over its pages as drawn. What the native
// side cannot see is where those layers should take clicks and touches from
// the page under them. The shell measures its own document with the kit's
// one rule (`drawnOverPage`: every box that shows something over the frame
// and paints above it) and sends the result as `shell.overlays`. No app reads
// the shell's DOM and no list of kinds is kept, so something the shell adds
// later is covered the same way. A shell from before page views went under
// the web UI cuts these rects out of every page instead.

import { useEffect } from "react";
import { drawnOverPage, native, type DrawnOver, type PageRect } from "@workshop/ui";

const NONE: DrawnOver = { rects: [], modal: false };
// A busy document (a reply streaming in the chat beside the app) mutates many
// times a second; a measure at most this often still follows a bar as it opens.
const MIN_GAP_MS = 80;

/** `r` grown to whole CSS pixels, so a box moving by a fraction is not news. */
function whole(r: PageRect): PageRect {
  const x = Math.floor(r.x);
  const y = Math.floor(r.y);
  return { x, y, width: Math.ceil(r.x + r.width) - x, height: Math.ceil(r.y + r.height) - y };
}

/** `frame`'s rect in page view space: the top visual viewport. */
function areaOf(frame: Element): PageRect | null {
  const r = frame.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return null;
  const vv = window.visualViewport;
  return { x: r.left - (vv?.offsetLeft ?? 0), y: r.top - (vv?.offsetTop ?? 0), width: r.width, height: r.height };
}

/** Keeps the native shell told what the shell draws over the shown app's
 *  frame, `active` in `elements` (none: every page is whole). */
export function useShellOverlays(active: string | null, elements: Map<string, HTMLIFrameElement>): void {
  useEffect(() => {
    const frame = active ? elements.get(active) : undefined;
    const bridge = native();
    const send = bridge?.shell?.overlays;
    if (!bridge?.has("shell") || typeof send !== "function") return;
    let sent = "";
    let timer = 0;
    let frameReq = 0;
    let lastAt = 0;
    const report = (d: DrawnOver) => {
      const rects = d.rects.map(whole);
      const key = JSON.stringify([rects, d.modal]);
      if (key === sent) return;
      sent = key;
      void send.call(bridge.shell, rects, d.modal).catch(() => {});
    };
    const measure = () => {
      frameReq = 0;
      lastAt = performance.now();
      report(frame?.isConnected ? drawnOverPage(frame, areaOf(frame)) : NONE);
    };
    const run = () => {
      if (frameReq || timer) return;
      const wait = MIN_GAP_MS - (performance.now() - lastAt);
      const go = () => { timer = 0; frameReq = requestAnimationFrame(measure); };
      if (wait > 0) timer = window.setTimeout(go, wait);
      else go();
    };
    const mo = new MutationObserver(run);
    mo.observe(document.body, {
      childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ["class", "style", "hidden", "open", "role", "aria-hidden", "popover"],
    });
    const settled = ["animationend", "transitionend"] as const;
    settled.forEach((type) => document.addEventListener(type, run, true));
    const vv = window.visualViewport;
    window.addEventListener("resize", run);
    vv?.addEventListener("resize", run);
    vv?.addEventListener("scroll", run);
    const resized = frame ? new ResizeObserver(run) : null;
    if (frame) resized?.observe(frame);
    run();
    return () => {
      mo.disconnect();
      resized?.disconnect();
      settled.forEach((type) => document.removeEventListener(type, run, true));
      window.removeEventListener("resize", run);
      vv?.removeEventListener("resize", run);
      vv?.removeEventListener("scroll", run);
      cancelAnimationFrame(frameReq);
      window.clearTimeout(timer);
      void send.call(bridge.shell, [], false).catch(() => {});
    };
  }, [active, elements]);
}
