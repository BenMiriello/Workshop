// Bringing a sentence onto the page when the page holds only part of what is
// read: an editor draws only the lines near its view, so the sentence being
// read may not be on the page at all. Its place is guessed from where it sits
// in the whole read, measured from a sentence that is on the page, the
// scroller is moved there, and the page is read again once it has drawn the
// lines; a few tries close in on it.

import type { Passage } from "./passage";

const TRIES = 4;

/** The element that scrolls `node`, out through same-origin frames. */
export function scrollerOf(node: Node): Element | null {
  let el: Element | null = node.nodeType === 1 ? node as Element : node.parentElement;
  while (el) {
    const doc = el.ownerDocument;
    if (el === doc.scrollingElement) {
      if (el.scrollHeight > el.clientHeight + 1) return el;
    } else {
      const s = doc.defaultView?.getComputedStyle(el);
      if (s && /(auto|scroll|overlay)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1) return el;
    }
    let up: Element | null = el.parentElement;
    if (!up) { try { up = doc.defaultView?.frameElement ?? null; } catch { up = null; } }
    el = up;
  }
  return null;
}

/** Whether sentence `i` is missing because the page holds only part of the
 *  read, rather than because it is not shown (a code block, an image). */
export function offPage(p: Passage, i: number): boolean {
  const shown = p.shown();
  return !shown || i < shown[0] || i > shown[1];
}

const frames = (doc: Document) => new Promise<void>((done) => {
  const win = doc.defaultView ?? window;
  win.requestAnimationFrame(() => win.requestAnimationFrame(() => done()));
});

// Where the scroller's content puts piece `i`, in its own scroll offsets.
function guess(p: Passage, i: number, scroller: Element): number {
  const total = scroller.scrollHeight;
  const shown = p.shown();
  const k = shown ? (i < shown[0] ? shown[0] : shown[1]) : null;
  const r = k === null ? null : p.rangeAt(k);
  if (k === null || !r) return p.share(i) * total;
  const doc = scroller.ownerDocument;
  const top = scroller === doc.scrollingElement ? 0 : scroller.getBoundingClientRect().top;
  return r.getBoundingClientRect().top - top + scroller.scrollTop + (p.share(i) - p.share(k)) * total;
}

/** Scroll until sentence `i` is on the page. Resolves with its range, or null
 *  when it cannot be brought there, or `still()` stops holding. */
export async function seek(p: Passage, i: number, still: () => boolean): Promise<Range | null> {
  for (let t = 0; t < TRIES; t++) {
    const r = p.rangeAt(i);
    if (r || !offPage(p, i)) return r;
    const node = p.shownNode;
    const scroller = node && scrollerOf(node);
    if (!scroller) return null;
    const to = Math.max(0, guess(p, i, scroller) - scroller.clientHeight * 0.3);
    if (Math.abs(to - scroller.scrollTop) < 1) return null;
    scroller.scrollTop = to;
    await frames(scroller.ownerDocument);
    if (!still()) return null;
  }
  return p.rangeAt(i);
}
