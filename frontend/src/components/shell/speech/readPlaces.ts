// Which text a tap reads from. A message or a document says what it reads as
// (`placeRead`: the same text and source its own Read aloud button uses), so a
// tap in it reads exactly what the button would, from the sentence tapped.
// Anywhere else a tap reads the page's own text, out to the nearest region a
// reader would call one piece: a dialog, an article, the page's main area.

import { flatten, asRange, type Point } from "./passage";

export type Readable = { el: Element; source: string; text: () => string };

const places = new WeakMap<Element, Omit<Readable, "el">>();
const ids = new WeakMap<Element, string>();
let nextId = 0;

/** Declare that `el` reads as `text()`, read for `source`. Returns the undo. */
export function placeRead(el: Element, source: string, text: () => string): () => void {
  const entry = { source, text };
  places.set(el, entry);
  return () => { if (places.get(el) === entry) places.delete(el); };
}

const REGION = '[role=dialog], article, main, [role=main]';

function sourceOf(el: Element): string {
  let id = ids.get(el);
  if (!id) { id = `page:${nextId++}`; ids.set(el, id); }
  return id;
}

/** What a tap at `node` reads, or null where nothing would. */
export function readableAt(node: Node): Readable | null {
  for (let el = node.nodeType === 1 ? node as Element : node.parentElement; el; el = el.parentElement) {
    if (el.matches('[data-read-aloud="off"]')) return null;
    const p = places.get(el);
    if (p) return { el, ...p };
  }
  const start = node.parentElement;
  const el = start?.closest(REGION) ?? start?.ownerDocument.body;
  if (!el) return null;
  return { el, source: sourceOf(el), text: () => flatten(asRange(el)).text };
}

type CaretDoc = Document & {
  caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  caretRangeFromPoint?: (x: number, y: number) => Range | null;
};

/** The point in the text under (x, y), in `doc`'s own coordinates, or null
 *  when the point is not over text (beside a line, between paragraphs). */
export function textAt(doc: Document, x: number, y: number): Point | null {
  const d = doc as CaretDoc;
  let p: Point | null = null;
  if (d.caretPositionFromPoint) {
    const c = d.caretPositionFromPoint(x, y);
    if (c) p = { node: c.offsetNode, offset: c.offset };
  } else if (d.caretRangeFromPoint) {
    const r = d.caretRangeFromPoint(x, y);
    if (r) p = { node: r.startContainer, offset: r.startOffset };
  }
  if (!p || p.node.nodeType !== 3) return null;
  const r = doc.createRange();
  r.selectNodeContents(p.node);
  const hit = Array.from(r.getClientRects()).some((b) =>
    x >= b.left - 2 && x <= b.right + 2 && y >= b.top - 2 && y <= b.bottom + 2);
  return hit ? p : null;
}
