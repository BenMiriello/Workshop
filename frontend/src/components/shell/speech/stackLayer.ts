// Which layer of the page a field is painted in, so something drawn over it
// from the shell (the ring around the field words go into) sits in that layer
// too: above the field, and under whatever covers the field (a menu, a dialog,
// a sticky header). Drawn on the top layer instead, the ring showed through
// all of them.
//
// The shell draws in the page's root stacking context, where the field is
// represented by its OUTERMOST ancestor that starts a stacking context of its
// own. That ancestor's z-index is the field's layer, 0 when none sets one.
// Across a frame the walk continues from the frame element.

function startsContext(s: CSSStyleDeclaration, parent: CSSStyleDeclaration | null): boolean {
  const z = s.zIndex !== "auto";
  const positioned = s.position !== "static";
  const inFlexOrGrid = !!parent && /flex|grid/.test(parent.display);
  return (z && (positioned || inFlexOrGrid))
    || s.position === "fixed" || s.position === "sticky"
    || Number(s.opacity) < 1 || s.transform !== "none" || s.filter !== "none"
    || s.isolation === "isolate" || /paint|layout|strict|content/.test(s.contain);
}

/** The z-index to draw at so as to share `el`'s layer. */
export function layerOf(el: Element): number {
  let z = 0;
  let at: Element | null = el;
  while (at) {
    const win: Window | null = at.ownerDocument.defaultView;
    const s = win?.getComputedStyle(at);
    const up: Element | null = at.parentElement ?? frameOf(at);
    const ps = up ? up.ownerDocument.defaultView?.getComputedStyle(up) ?? null : null;
    if (s && at !== at.ownerDocument.documentElement && startsContext(s, ps)) {
      z = s.zIndex === "auto" ? 0 : Number(s.zIndex) || 0;
    }
    at = up;
  }
  return z;
}

function frameOf(el: Element): Element | null {
  if (el !== el.ownerDocument.documentElement) return null;
  try { return el.ownerDocument.defaultView?.frameElement ?? null; } catch { return null; }
}
