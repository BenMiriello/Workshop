// KEEP THE CARET ON SCREEN WHEN THE WINDOW GETS SHORTER UNDER IT.
//
// On a phone the tap that opens the keyboard is revealed by iOS BEFORE the
// shell shrinks to the visible window (lib/useVisualViewport): the browser
// scrolls the line it is about to cover into view, then the shell takes the
// keyboard's height off its bottom, and the editor's own scroller ends where
// the keyboard begins. Nothing scrolls that scroller, so the line just tapped
// is now below its bottom edge, hidden, with the bars correctly on screen.
// Measured on iOS WebKit 2026-10-25 with a note's editor: the caret 160px
// under the keyboard.
//
// A text field or textarea is revealed by the browser on its own; what it does
// not do is follow a caret inside a contenteditable (CodeMirror, any rich
// editor) once its box has been resized. So this does, once per shrink, by
// scrolling each scroller between the caret and the shell just enough.

/** A vertical span, in one coordinate space. */
export type Span = { top: number; bottom: number };

/** How far to scroll a box so `caret` sits inside `box` with `margin` to
 *  spare. Positive scrolls down (the caret is below), negative up, 0 when it
 *  is already in view. A caret taller than the box is aligned by its top. */
export function revealDelta(caret: Span, box: Span, margin: number): number {
  const top = box.top + margin;
  const bottom = box.bottom - margin;
  if (caret.top < top || caret.bottom - caret.top > bottom - top) return caret.top - top;
  if (caret.bottom > bottom) return caret.bottom - bottom;
  return 0;
}

const MARGIN_PX = 8;

function caretRect(): DOMRect | null {
  const sel = document.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0).cloneRange();
  range.collapse(false);
  const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
  if (rect && (rect.height > 0 || rect.top !== 0)) return rect;
  // A collapsed range on an empty line has no box; its line does.
  const node = range.endContainer;
  const el = node instanceof Element ? node : node.parentElement;
  return el ? el.getBoundingClientRect() : null;
}

function scrolls(el: Element): boolean {
  const y = getComputedStyle(el).overflowY;
  return (y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight;
}

/** Scroll the focused contenteditable's scrollers so its caret is in view. */
export function revealCaret(): void {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !active.isContentEditable) return;
  for (let el: Element | null = active; el && el !== document.body; el = el.parentElement) {
    if (!scrolls(el)) continue;
    const caret = caretRect();
    if (!caret) return;
    const box = el.getBoundingClientRect();
    const delta = revealDelta(caret, box, MARGIN_PX);
    if (delta !== 0) el.scrollTop += delta;
  }
}
