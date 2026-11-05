// The DOM half of find-in-page: turn a query into Ranges over the text of
// everything on screen (the content region, the chat beside it, and every app
// frame in either), paint them, and bring the current one into view.
//
// An app frame is another document, so it has its own highlight registry and
// its own scrollers. A frame this page cannot reach into (another origin) is
// not searched.
//
// TEXT A PAGE HIDES IS STILL FOUND. A block kept `hidden="until-found"` (a
// collapsed disclosure while a search runs, see useOnFound) counts, and so does
// text clipped by a clamp. Showing such a match fires `beforematch` at it, which
// is what opens the thing hiding it. Text marked `data-find-skip` is a preview
// of something the search already reaches in full, and is not counted twice.

import { frameDocument } from "../sameOriginFrames";
import { MAX_MATCHES, matchOffsets } from "./matchText";
import { FOUND_EVENT } from "./useOnFound";

const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT"]);
const HIDDEN = '[hidden="until-found"]';
/** Frames in frames are searched, to a bound a real page never reaches. */
const MAX_SCOPES = 12;

export type Scope = { doc: Document; root: Element };

type Win = Window & typeof globalThis;

function onScreen(el: Element): boolean {
  const win = el.ownerDocument.defaultView;
  return !!win && win.getComputedStyle(el).visibility === "visible" && el.getClientRects().length > 0;
}

/** The places to search: the content region, the chat beside it when open,
 *  and each frame in them that is on screen and readable, nested ones too. */
export function searchScopes(main: Element): Scope[] {
  const scopes: Scope[] = [{ doc: document, root: main }];
  const beside = document.querySelector(".ws-beside");
  if (beside && onScreen(beside)) scopes.push({ doc: document, root: beside });
  for (let i = 0; i < scopes.length; i++) {
    for (const frame of scopes[i].root.querySelectorAll("iframe")) {
      if (scopes.length >= MAX_SCOPES) return scopes;
      const doc = frameDocument(frame);
      if (!doc?.body || !onScreen(frame)) continue;
      scopes.push({ doc, root: doc.body });
    }
  }
  return scopes;
}

function visibilityCheck() {
  const seen = new Map<Element, boolean>();
  return (el: Element) => {
    let v = seen.get(el);
    if (v === undefined) {
      v = typeof el.checkVisibility === "function" ? el.checkVisibility() : true;
      seen.set(el, v);
    }
    return v;
  };
}

/** Shown, or kept until found inside something that is shown; and not a
 *  preview of text counted elsewhere. */
function findableCheck() {
  const visible = visibilityCheck();
  return (el: Element) => {
    if (el.closest("[data-find-skip]")) return false;
    if (visible(el)) return true;
    let outer: Element | null = null;
    for (let h = el.closest(HIDDEN); h; h = h.parentElement?.closest(HIDDEN) ?? null) outer = h;
    return !!outer?.parentElement && visible(outer.parentElement);
  };
}

export function collectRanges(scopes: Scope[], query: string): Range[] {
  const ranges: Range[] = [];
  if (!query) return ranges;
  for (const { doc, root } of scopes) {
    const visible = findableCheck();
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || SKIP.has(parent.tagName)) continue;
      const text = node.nodeValue ?? "";
      if (!text.trim()) continue;
      const at = matchOffsets(text, query);
      if (!at.length || !visible(parent)) continue;
      for (const start of at) {
        const r = doc.createRange();
        r.setStart(node, start);
        r.setEnd(node, start + query.length);
        ranges.push(r);
        if (ranges.length >= MAX_MATCHES) return ranges;
      }
    }
  }
  return ranges;
}

const ALL = "ws-find";
const CURRENT = "ws-find-current";
const STYLE_ATTR = "data-ws-find";

const windowOf = (r: Range) => r.startContainer.ownerDocument?.defaultView ?? null;
const supports = (win: Win) => "highlights" in win.CSS;

// One Highlight per name per window, registered once and mutated afterwards.
// Swapping in a new Highlight object (or deleting the registry entry) is not
// reliably painted by every engine, so a replaced or removed highlight could
// stay on screen; emptying the one registered object always notifies the
// painter.
const layers = new WeakMap<Win, Map<string, Highlight>>();
const touched = new Set<Win>();

function layer(win: Win, name: string): Highlight {
  let named = layers.get(win);
  if (!named) layers.set(win, (named = new Map()));
  let h = named.get(name);
  if (!h) {
    h = new win.Highlight();
    named.set(name, h);
  }
  win.CSS.highlights.set(name, h);
  return h;
}

function fill(win: Win, name: string, ranges: Range[]): void {
  const h = layer(win, name);
  h.clear();
  for (const r of ranges) h.add(r);
}

/** A frame has none of the page's tokens, so it gets the page's current values
 *  written out as literals. */
function styleFrame(win: Win): void {
  const doc = win.document;
  if (doc === document) return;
  const tokens = getComputedStyle(document.documentElement);
  const v = (name: string) => tokens.getPropertyValue(name).trim();
  let style = doc.head.querySelector(`style[${STYLE_ATTR}]`);
  if (!style) {
    style = doc.createElement("style");
    style.setAttribute(STYLE_ATTR, "");
    doc.head.appendChild(style);
  }
  style.textContent =
    `::highlight(${ALL}){background-color:color-mix(in srgb,${v("--fg")} 30%,transparent);color:inherit}` +
    `::highlight(${CURRENT}){background-color:${v("--you")};color:${v("--you-fg")}}`;
}

/** Paint every match, and the current one in a stronger tone. Without the CSS
 *  Custom Highlight API the current match is selected instead. */
export function paint(ranges: Range[], current: number): void {
  const byWindow = new Map<Win, Range[]>();
  for (const r of ranges) {
    const win = windowOf(r);
    if (win) byWindow.set(win, [...(byWindow.get(win) ?? []), r]);
  }
  const wins = new Set<Win>([...touched, ...byWindow.keys()]);
  const now = ranges[current];
  for (const win of wins) {
    if (!supports(win)) {
      const sel = win.getSelection();
      sel?.removeAllRanges();
      if (now && windowOf(now) === win) sel?.addRange(now);
      continue;
    }
    styleFrame(win);
    fill(win, ALL, byWindow.get(win) ?? []);
    fill(win, CURRENT, now && windowOf(now) === win ? [now] : []);
  }
  touched.clear();
  for (const win of byWindow.keys()) touched.add(win);
}

export function clearPaint(): void {
  for (const win of [...touched, window]) {
    try {
      if (!supports(win)) {
        win.getSelection()?.removeAllRanges();
        continue;
      }
      for (const [name, h] of layers.get(win) ?? []) {
        h.clear();
        win.CSS.highlights.delete(name);
      }
      layers.delete(win);
    } catch {
      // A frame that has since navigated away or been removed has nothing to clear.
    }
  }
  touched.clear();
}

/** Cut off by a clip between it and its nearest scroller (a clamp): out of
 *  sight in a way scrolling cannot fix. */
function clipped(range: Range, el: Element, win: Win): boolean {
  const at = range.getBoundingClientRect();
  for (let a = el.parentElement; a && a !== win.document.body; a = a.parentElement) {
    const { overflowY } = win.getComputedStyle(a);
    if (overflowY === "auto" || overflowY === "scroll") return false;
    if (overflowY !== "hidden" && overflowY !== "clip") continue;
    const box = a.getBoundingClientRect();
    if (at.top < box.top - 1 || at.bottom > box.bottom + 1) return true;
  }
  return false;
}

/** Kept until found, or clipped: on the page but not in sight. */
export function isHiddenMatch(range: Range): boolean {
  const el = range.startContainer.parentElement;
  const win = windowOf(range);
  return !!el && !!win && (!!el.closest(HIDDEN) || clipped(range, el, win));
}

/** Ask whatever hides `range` to open, by firing `beforematch` at it. True
 *  when it was hidden, so the caller knows the page is about to change. */
export function exposeMatch(range: Range): boolean {
  const el = range.startContainer.parentElement;
  const win = windowOf(range);
  if (!el || !win || !isHiddenMatch(range)) return false;
  el.dispatchEvent(new win.Event(FOUND_EVENT, { bubbles: true }));
  return true;
}

/** Centre `range` in whichever scrollers between it and `stopAt` hide it. Walks
 *  the ancestors itself rather than calling scrollIntoView, which would also
 *  scroll overflow:hidden ancestors (the shell) and shift the whole app. In a
 *  frame there is no `stopAt`: the walk runs up to the frame's own page scroll. */
export function reveal(range: Range, stopAt: Element | null): void {
  const target = range.startContainer.parentElement;
  const win = windowOf(range);
  if (!target || !win) return;
  const page = win.document.scrollingElement;
  for (let el: Element | null = target.parentElement; el; el = el.parentElement) {
    const { overflowY } = win.getComputedStyle(el);
    const scrolls = overflowY === "auto" || overflowY === "scroll" || el === page;
    if (scrolls && el.scrollHeight > el.clientHeight) {
      const { top, height } = el === page ? { top: 0, height: win.innerHeight } : el.getBoundingClientRect();
      const at = range.getBoundingClientRect();
      const margin = height * 0.2;
      if (at.top < top + margin || at.bottom > top + height - margin) {
        el.scrollTop += at.top - top - height / 2 + at.height / 2;
      }
    }
    if (el === stopAt) break;
  }
}
