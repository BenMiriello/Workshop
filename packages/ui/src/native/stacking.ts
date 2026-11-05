// Paint order between two elements of one document, from their computed
// styles: which one the browser draws on top where they overlap. It follows
// CSS stacking (Appendix E of CSS 2): stacking contexts nest, z-index orders
// siblings within one, a positioned or stacking box draws over the in-flow
// boxes of its context, and tree order breaks ties. Used to tell what the web
// UI draws over a native page view (./layersOver.ts).

type Level = { el: Element; z: number };

const STATIC_Z = new Set(["flex", "inline-flex", "grid", "inline-grid"]);

/** Whether `el` starts a stacking context of its own. */
function stacks(el: Element, cs: CSSStyleDeclaration): boolean {
  if (cs.position === "fixed" || cs.position === "sticky") return true;
  if (cs.zIndex !== "auto" && (cs.position !== "static" || inFlexOrGrid(el))) return true;
  return Number(cs.opacity) < 1 || cs.transform !== "none" || cs.filter !== "none"
    || cs.isolation === "isolate" || cs.mixBlendMode !== "normal"
    || (cs.getPropertyValue("backdrop-filter") || "none") !== "none"
    || cs.perspective !== "none" || cs.clipPath !== "none"
    || /\b(transform|opacity|filter)\b/.test(cs.willChange)
    || /\b(paint|layout|strict|content)\b/.test(cs.contain);
}

function inFlexOrGrid(el: Element): boolean {
  const parent = el.parentElement;
  const view = el.ownerDocument.defaultView;
  return !!parent && !!view && STATIC_Z.has(view.getComputedStyle(parent).display);
}

/** Whether the browser keeps `el` in the top layer (an open modal dialog, a
 *  shown popover, an element in full screen), above everything else. */
export function inTopLayer(el: Element): boolean {
  try {
    return el.matches(":modal, :popover-open, :fullscreen");
  } catch {
    return false;
  }
}

/** What orders `el` against the rest of its document, outermost first: each
 *  ancestor-or-self that starts a stacking context, at the z-index it is
 *  placed at (auto counts as 0), then the box `el` paints with inside the
 *  last of them: the nearest positioned ancestor-or-self below it, at 0. A
 *  positioned box with z-index auto starts no context, so a box with a
 *  z-index inside it is placed in the context around it, not under it. */
function levels(el: Element): Level[] {
  const view = el.ownerDocument.defaultView;
  const out: Level[] = [];
  if (!view) return out;
  const chain: Element[] = [];
  for (let at: Element | null = el; at; at = at.parentElement) chain.unshift(at);
  let painter: Level | null = null;
  for (const at of chain) {
    const cs = view.getComputedStyle(at);
    if (stacks(at, cs)) {
      const z = cs.position !== "static" || inFlexOrGrid(at) ? parseInt(cs.zIndex, 10) : NaN;
      out.push({ el: at, z: Number.isNaN(z) ? 0 : z });
      painter = null;
    } else if (cs.position !== "static") {
      painter = { el: at, z: 0 };
    }
  }
  if (painter) out.push(painter);
  return out;
}

/** Whether `a` is drawn over `b`, two elements of one document. */
export function drawnOver(a: Element, b: Element): boolean {
  if (inTopLayer(a) !== inTopLayer(b)) return inTopLayer(a);
  const pa = levels(a);
  const pb = levels(b);
  let i = 0;
  while (i < pa.length && i < pb.length && pa[i].el === pb[i].el) i++;
  const la = pa[i];
  const lb = pb[i];
  // One of them is in the flow of the context they share, under every
  // positioned box at z-index 0 or more in it and over those below 0.
  if (!la && !lb) return !!(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);
  if (!la) return lb.z < 0;
  if (!lb) return la.z >= 0;
  if (la.z !== lb.z) return la.z > lb.z;
  return !!(lb.el.compareDocumentPosition(la.el) & Node.DOCUMENT_POSITION_FOLLOWING);
}
