// What a document draws over a native page view, which is where clicks and
// touches go to the web UI instead of the page (`pages.setCutouts`,
// `shell.overlays`). A page view is drawn under the web UI, so what shows is
// the engine's own rendering (./underPage.ts); this decides input only, and a
// shell from before that cuts the page away at these rects. Which elements
// those are is read from the document itself, never from a list of kinds:
// every box that shows something, overlaps the page, and paints over the
// element the page fills (./stacking.ts). A bar, a notice, a tooltip or a
// menu added later is covered by the same rule.

import { drawnOver } from "./stacking";
import type { PageRect } from "./types";

export interface DrawnOver {
  /** The boxes drawn over the page area, in the same space as page view
   *  rects (the top document's visual viewport), each cut to the area. */
  rects: PageRect[];
  /** Whether one of them is a menu, dialog or list, which takes every click
   *  and touch until it closes, as a page under an open menu does. */
  modal: boolean;
}

const NONE: DrawnOver = { rects: [], modal: false };
const MODAL = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [aria-modal="true"]';
const REPLACED = new Set(["img", "video", "canvas", "svg", "iframe", "input", "textarea", "select", "picture", "embed", "object"]);

/** The alpha of a computed color, 0 for transparent. */
function alpha(color: string): number {
  if (!color || color === "transparent") return 0;
  const slash = color.match(/\/\s*([\d.]+)(%?)\s*\)$/);
  if (slash) return Number(slash[1]) / (slash[2] ? 100 : 1);
  const parts = color.match(/^rgba?\(([^)]*)\)$/)?.[1].split(",");
  return parts && parts.length === 4 ? Number(parts[3]) : 1;
}

function bordered(cs: CSSStyleDeclaration): boolean {
  return (["Top", "Right", "Bottom", "Left"] as const).some((side) =>
    parseFloat(cs.getPropertyValue(`border-${side.toLowerCase()}-width`)) > 0
    && cs.getPropertyValue(`border-${side.toLowerCase()}-style`) !== "none"
    && alpha(cs.getPropertyValue(`border-${side.toLowerCase()}-color`)) > 0);
}

/** Whether `el` shows something of its own, as opposed to only holding
 *  boxes that do. A dimmed scrim does: a click on it closes its dialog. */
function paints(el: Element, cs: CSSStyleDeclaration): boolean {
  if (REPLACED.has(el.localName)) return true;
  if (alpha(cs.backgroundColor) > 0 || cs.backgroundImage !== "none") return true;
  if (cs.boxShadow !== "none" || bordered(cs)) return true;
  for (const node of el.childNodes) {
    if (node.nodeType === Node.TEXT_NODE && node.textContent?.trim()) return true;
  }
  return false;
}

function shown(el: Element, cs: CSSStyleDeclaration): boolean {
  if (cs.visibility !== "visible") return false;
  const check = (el as Element & { checkVisibility?: (o: object) => boolean }).checkVisibility;
  return !check || check.call(el, { opacityProperty: true, visibilityProperty: true });
}

/** Where `doc`'s viewport sits in the top document's visual viewport. */
function docOffset(doc: Document): { x: number; y: number } {
  let x = 0;
  let y = 0;
  try {
    let win: Window | null = doc.defaultView;
    while (win && win.parent !== win && win.frameElement) {
      const frame = win.frameElement as HTMLElement;
      const f = frame.getBoundingClientRect();
      x += f.left + frame.clientLeft;
      y += f.top + frame.clientTop;
      win = win.parent;
    }
    const vv = win?.visualViewport;
    x -= vv?.offsetLeft ?? 0;
    y -= vv?.offsetTop ?? 0;
  } catch {
    // Stop at a cross-origin ancestor.
  }
  return { x, y };
}

/** `r`, `el`'s box in viewport pixels, less what its ancestors' overflow
 *  clips away (a rail's tiles wrapped out of sight are not drawn). A fixed
 *  box escapes the clips of everything around it. */
function unclipped(el: Element, r: PageRect, view: Window): PageRect | null {
  let out: PageRect | null = r;
  let fixed = view.getComputedStyle(el).position === "fixed";
  for (let up = el.parentElement; up && out && !fixed && up !== el.ownerDocument.body; up = up.parentElement) {
    const cs = view.getComputedStyle(up);
    if (cs.overflowX !== "visible" || cs.overflowY !== "visible") {
      const c = up.getBoundingClientRect();
      out = clip(out, { x: c.left, y: c.top, width: c.width, height: c.height });
    }
    fixed = cs.position === "fixed";
  }
  return out;
}

function clip(r: PageRect, area: PageRect): PageRect | null {
  const x = Math.max(r.x, area.x);
  const y = Math.max(r.y, area.y);
  const width = Math.min(r.x + r.width, area.x + area.width) - x;
  const height = Math.min(r.y + r.height, area.y + area.height) - y;
  return width > 0 && height > 0 ? { x, y, width, height } : null;
}

/**
 * What `host`'s document draws over `area`, the part of the screen a page
 * view takes (in page view space). `host` is the element the page view fills
 * or stands in for (an app's frame, an app's stage): it, what it holds and
 * what holds it are never counted, and nothing drawn under it is.
 */
export function drawnOverPage(host: Element, area: PageRect | null): DrawnOver {
  const doc = host.ownerDocument;
  const view = doc.defaultView;
  if (!area || !view || !doc.body) return NONE;
  const off = docOffset(doc);
  // A box inside one already counted adds nothing to its hole.
  const over = new Set<Element>();
  const rects: PageRect[] = [];
  let modal = false;
  for (const el of doc.body.getElementsByTagName("*")) {
    if (el === host || el.contains(host) || host.contains(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const box = { x: r.left, y: r.top, width: r.width, height: r.height };
    if (!clip({ ...box, x: box.x + off.x, y: box.y + off.y }, area)) continue;
    const cs = view.getComputedStyle(el);
    if (!paints(el, cs) || !shown(el, cs) || within(el, over)) continue;
    const seen = unclipped(el, box, view);
    const rect = seen && clip({ ...seen, x: seen.x + off.x, y: seen.y + off.y }, area);
    if (!rect || !drawnOver(el, host)) continue;
    over.add(el);
    rects.push(rect);
    modal ||= !!el.closest(MODAL) || isModal(el);
  }
  return rects.length ? { rects, modal } : NONE;
}

function within(el: Element, set: Set<Element>): boolean {
  for (let up = el.parentElement; up; up = up.parentElement) if (set.has(up)) return true;
  return false;
}

function isModal(el: Element): boolean {
  try {
    return el.matches(":modal");
  } catch {
    return false;
  }
}
