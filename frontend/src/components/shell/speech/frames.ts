// Where things are across the shell and its same-origin frames: the focused
// field, a box in shell coordinates, the selected text.

import { chooseAnchor, type AnchorCandidate } from "../../../lib/speech/anchor";
import type { Box } from "../../../lib/speech/placement";
import { frameDocument } from "../sameOriginFrames";

export { watchDocuments } from "../sameOriginFrames";

/** Whether an element is drawn: not hidden, not undisplayed, not inert. A
 *  frame's visibility does not reach the document inside it, so a field in a
 *  hidden frame reads as visible on its own. */
export function shown(el: Element): boolean {
  const s = el.ownerDocument.defaultView?.getComputedStyle(el);
  return !!s && s.visibility === "visible" && s.display !== "none" && !el.closest("[inert]");
}

/** Whether a document is on screen: every frame it sits in, out to the shell,
 *  is shown. */
export function documentShown(doc: Document): boolean {
  for (let win: Window | null = doc.defaultView; win && win !== window; ) {
    let frame: Element | null = null;
    try { frame = win.frameElement; } catch { return true; }
    if (!frame) return true;
    if (!shown(frame)) return false;
    win = frame.ownerDocument.defaultView;
  }
  return true;
}

/** The element that has focus, looking through frames to the innermost one.
 *  A frame that is not shown stops the search: the shell keeps an app's page
 *  loaded but hidden after it is left, and on a touch screen tapping the shell
 *  does not move focus, so the field focused in that page still reads as
 *  focused there. */
export function deepActiveElement(): Element | null {
  let el: Element | null = document.activeElement;
  while (el && el.tagName === "IFRAME") {
    if (!shown(el)) return el;
    const inner = frameDocument(el as HTMLIFrameElement)?.activeElement ?? null;
    if (!inner || inner === inner.ownerDocument.body) return el;
    el = inner;
  }
  return el;
}

/** Where a document's viewport origin sits in the shell window. */
export function frameOffset(doc: Document): { x: number; y: number } {
  let x = 0;
  let y = 0;
  let win: Window | null = doc.defaultView;
  while (win && win !== window) {
    let frame: Element | null = null;
    try { frame = win.frameElement; } catch { return { x, y }; }
    if (!frame) break;
    const r = frame.getBoundingClientRect();
    x += r.left + frame.clientLeft;
    y += r.top + frame.clientTop;
    win = frame.ownerDocument.defaultView;
  }
  return { x, y };
}

function shift(r: DOMRect, doc: Document): Box {
  const o = frameOffset(doc);
  return { left: r.left + o.x, top: r.top + o.y, right: r.right + o.x, bottom: r.bottom + o.y };
}

/** An element's box in the shell window's coordinates. */
export function boxOf(el: Element): Box {
  return shift(el.getBoundingClientRect(), el.ownerDocument);
}

// How far out from the focused element to look for the area it stands for.
const ANCHOR_DEPTH = 12;

function candidate(el: Element): AnchorCandidate {
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  const visible = !style || (style.visibility !== "hidden" && Number(style.opacity || "1") > 0.05);
  return { box: boxOf(el), marked: el.hasAttribute("data-dictation-anchor"), visible };
}

/** What the dictation chip is placed against: the focused field, or the
 *  area it stands in for when the field itself is a hidden helper. Climbs out
 *  of a frame into its frame element when the frame's own tree runs out. */
export function anchorOf(field: Element): { el: Element; box: Box } {
  const chain: { el: Element; c: AnchorCandidate }[] = [];
  let el: Element | null = field;
  while (el && chain.length < ANCHOR_DEPTH) {
    chain.push({ el, c: candidate(el) });
    let next: Element | null = el.parentElement;
    if (!next) {
      try { next = el.ownerDocument.defaultView?.frameElement ?? null; } catch { next = null; }
    }
    el = next;
  }
  const pick = chain[chooseAnchor(chain.map((x) => x.c))];
  return pick ? { el: pick.el, box: pick.c.box } : { el: field, box: boxOf(field) };
}

const CLIPS = /auto|scroll|hidden|clip/;

function cut(a: Box, b: Box): Box | null {
  const out = {
    left: Math.max(a.left, b.left), top: Math.max(a.top, b.top),
    right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom),
  };
  return out.right > out.left && out.bottom > out.top ? out : null;
}

/** The part of `box` (an element's box in shell coordinates) that can be
 *  seen: cut to every scrolling or clipping ancestor and to the viewport of
 *  every document it is in, frame by frame out to the shell. A field scrolled
 *  out of its panel, or out of an app frame, has none, and gets no chip; nor
 *  does one that is hidden, or in a hidden frame. */
export function visibleBox(el: Element, box: Box): Box | null {
  if (!shown(el)) return null;
  let clip: Box | null = box;
  let node: Element | null = el.parentElement;
  let doc: Document = el.ownerDocument;
  while (clip) {
    const win = doc.defaultView;
    if (!win) return null;
    for (; node && node !== doc.body && node !== doc.documentElement; node = node.parentElement) {
      const s = win.getComputedStyle(node);
      if (!CLIPS.test(s.overflowX) && !CLIPS.test(s.overflowY)) continue;
      const r = shift(node.getBoundingClientRect(), doc);
      const inner = {
        left: r.left + node.clientLeft, top: r.top + node.clientTop,
        right: r.left + node.clientLeft + node.clientWidth, bottom: r.top + node.clientTop + node.clientHeight,
      };
      clip = cut(clip, inner);
      if (!clip) return null;
    }
    const o = frameOffset(doc);
    const root = doc.documentElement;
    clip = cut(clip, { left: o.x, top: o.y, right: o.x + root.clientWidth, bottom: o.y + root.clientHeight });
    let frame: Element | null = null;
    try { frame = win.frameElement; } catch { frame = null; }
    if (!frame) break;
    if (!shown(frame)) return null;
    node = frame.parentElement;
    doc = frame.ownerDocument;
  }
  return clip;
}

/** Whether an event target sits inside `data-dictation="off"`, looking out
 *  through the frames it is in, so an app can mark a whole frame. */
export function speechOptedOut(target: EventTarget | null): boolean {
  const node = target as Node | null;
  if (!node || typeof node.nodeType !== "number") return false;
  let el: Element | null = node.nodeType === 1 ? node as Element : node.parentElement;
  while (el) {
    if (el.closest('[data-dictation="off"]')) return true;
    try { el = el.ownerDocument.defaultView?.frameElement ?? null; } catch { el = null; }
  }
  return false;
}

/** A range's box in the shell window's coordinates. */
export function rangeBox(range: Range): Box {
  return shift(range.getBoundingClientRect(), range.startContainer.ownerDocument ?? document);
}

/** Every reachable document, the focused chain first. */
function documentsByFocus(): Document[] {
  const out: Document[] = [];
  const active = deepActiveElement();
  for (let doc: Document | null = active?.ownerDocument ?? null; doc; ) {
    out.push(doc);
    const frame = doc === document ? null : doc.defaultView?.frameElement ?? null;
    doc = frame ? frame.ownerDocument : null;
  }
  const walk = (doc: Document) => {
    if (!out.includes(doc)) out.push(doc);
    doc.querySelectorAll("iframe").forEach((f) => {
      const d = shown(f) ? frameDocument(f) : null;
      if (d) walk(d);
    });
  };
  walk(document);
  return out;
}

/** The text selected anywhere, preferring the focused field and then the
 *  focused document, with its range when it is in a document's text. Empty
 *  when nothing is selected. */
export function selection(): { text: string; range: Range | null } {
  const active = deepActiveElement() as HTMLInputElement | null;
  if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) {
    try {
      const s = active.selectionStart;
      const e = active.selectionEnd;
      if (s != null && e != null && e > s) return { text: active.value.slice(s, e).trim(), range: null };
    } catch {
      // An input type without a selection API.
    }
  }
  for (const doc of documentsByFocus()) {
    const sel = doc.getSelection();
    const text = sel?.toString().trim();
    if (text) return { text, range: sel!.rangeCount ? sel!.getRangeAt(0).cloneRange() : null };
  }
  return { text: "", range: null };
}

const CLEAR = 4;

// What counts as one of the page's own controls, which the chip must not cover.
const CONTROL = [
  "button", "a[href]", "select", "summary", "input:not([type=hidden])", "textarea",
  "[contenteditable]:not([contenteditable=false])",
  "[role=button]", "[role=link]", "[role=menuitem]", "[role=tab]", "[role=switch]", "[role=checkbox]",
].join(",");

/** Whether a chip at `chip` (shell coordinates) would cover a control other
 *  than `field`: anything clickable stacked over the field or beside it. The
 *  field and what contains it are where the chip belongs, so the search stops
 *  there. */
export function coversControl(field: Element, chip: Box): boolean {
  const doc = field.ownerDocument;
  const o = frameOffset(doc);
  // A few pixels clear of the chip as well, so it never touches a control.
  const l = chip.left - o.x - CLEAR;
  const t = chip.top - o.y;
  const r = chip.right - o.x + CLEAR;
  const b = chip.bottom - o.y;
  const mid = (t + b) / 2;
  const points: [number, number][] = [
    [(l + r) / 2, mid], [l, mid], [r, mid], [l, t + 2], [r, t + 2], [l, b - 2], [r, b - 2],
  ];
  for (const [x, y] of points) {
    for (const el of doc.elementsFromPoint(x, y)) {
      if (el.closest(".ws-speech-layer")) continue;
      if (el === field || el.contains(field)) break;
      const control = el.closest(CONTROL);
      if (control && control !== field && !control.contains(field)) return true;
    }
  }
  return false;
}
