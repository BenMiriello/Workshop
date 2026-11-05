// A read laid over the page: the text of a place (an element or a selected
// range) as the page shows it, and where each sentence being read sits in it,
// as a Range (lib/speech/align.ts finds them). Highlighting, following and
// tapping to read from a sentence all go through this.
//
// The page is read again when it has changed and a sentence has no place, or
// lost it: an editor puts only the lines near its view on the page and
// replaces them as it scrolls (passage of a long note).
//
// The text skips what is not read: controls, navigation, code blocks, anything
// hidden or marked `data-read-aloud="off"`. A block boundary is a line break,
// so a heading never runs into the paragraph under it.

import { alignPieces, pieceAt, tokens, type Span } from "../../../lib/speech/align";

const SKIP = [
  "script", "style", "noscript", "template", "svg", "math", "pre", "button", "select", "textarea", "input",
  "nav", "[role=navigation]", "[role=toolbar]", "[role=menu]", "[role=menubar]", "[role=tablist]",
  "[aria-hidden=true]", "[hidden]", "[inert]", '[data-read-aloud="off"]',
].join(",");

type Chunk = { node: Text; from: number; at: number; len: number };
export type Flat = { text: string; chunks: Chunk[] };
export type Point = { node: Node; offset: number };

function rendered(el: Element): boolean {
  const e = el as Element & { checkVisibility?: () => boolean };
  return e.checkVisibility ? e.checkVisibility() : el.getClientRects().length > 0;
}

function isBlock(el: Element): boolean {
  const d = el.ownerDocument.defaultView?.getComputedStyle(el).display ?? "inline";
  return !d.startsWith("inline") && d !== "contents";
}

export function asRange(place: Element | Range): Range {
  if (!("nodeType" in place)) return place;
  const r = place.ownerDocument.createRange();
  r.selectNodeContents(place);
  return r;
}

/** The page text of `place`, with where each run of it came from. */
export function flatten(place: Range): Flat {
  const root = place.commonAncestorContainer;
  const doc = root.ownerDocument ?? (root as Document);
  const chunks: Chunk[] = [];
  let text = "";
  let block: Element | null = null;
  const blockOf = (el: Element | null) => {
    while (el && !isBlock(el)) el = el.parentElement;
    return el;
  };
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      if (n.nodeType === 3) return NodeFilter.FILTER_ACCEPT;
      const el = n as Element;
      if (el.matches(SKIP) || !rendered(el)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_SKIP;
    },
  });
  if (root.nodeType === 1 && ((root as Element).closest(SKIP))) return { text, chunks };
  for (let n = root.nodeType === 3 ? root : walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType !== 3 || !place.intersectsNode(n)) continue;
    const node = n as Text;
    const from = node === place.startContainer ? place.startOffset : 0;
    const to = node === place.endContainer ? place.endOffset : node.data.length;
    const piece = node.data.slice(from, to);
    if (!piece.trim()) {
      if (piece && text && !/\s$/.test(text)) text += " ";
      continue;
    }
    const b = blockOf(node.parentElement);
    if (text && b !== block) text += "\n";
    else if (node.previousSibling?.nodeName === "BR") text += "\n";
    block = b;
    chunks.push({ node, from, at: text.length, len: piece.length });
    text += piece;
    if (root.nodeType === 3) break;
  }
  return { text, chunks };
}

/** The page-text offset of a point, or null when it is not in the text. */
export function offsetOf(flat: Flat, p: Point): number | null {
  const c = flat.chunks.find((x) => x.node === p.node);
  if (!c) return null;
  return c.at + Math.min(c.len, Math.max(0, p.offset - c.from));
}

function pointAt(flat: Flat, offset: number, end: boolean): Point | null {
  for (const c of flat.chunks) {
    const inside = end ? offset > c.at && offset <= c.at + c.len : offset >= c.at && offset < c.at + c.len;
    if (inside) return { node: c.node, offset: c.from + offset - c.at };
  }
  return null;
}

export class Passage {
  private ranges = new Map<number, Range>();
  private changed = false;
  private watching: MutationObserver | null = null;
  /** Where each piece starts in the whole read, as a share of it: where its
   *  text is likely to be in a place that shows only part of it. */
  private shares: number[];

  private constructor(
    private readonly source: Element | Range,
    private place: Range,
    private flat: Flat,
    private spans: (Span | null)[],
    private readonly pieces: string[],
  ) {
    const total = pieces.reduce((n, p) => n + p.length, 0) || 1;
    let at = 0;
    this.shares = pieces.map((p) => { const s = at / total; at += p.length; return s; });
  }

  /** `pieces` (the sentences being read) laid over `place`, or null when the
   *  page shows none of them. */
  static over(place: Element | Range, pieces: string[]): Passage | null {
    const range = asRange(place);
    const flat = flatten(range);
    const spans = alignPieces(pieces, tokens(flat.text));
    return spans.some((s) => s) ? new Passage(place, range, flat, spans, pieces) : null;
  }

  get doc(): Document { return this.place.startContainer.ownerDocument ?? document; }

  /** Watch the page for changes, so a sentence it puts on the page later is
   *  found; for the read in progress, until `close`. */
  watch(): void {
    if (this.watching) return;
    const root = "nodeType" in this.source ? this.source : this.source.commonAncestorContainer;
    const Observer = root.ownerDocument?.defaultView?.MutationObserver ?? MutationObserver;
    this.watching = new Observer(() => { this.changed = true; });
    this.watching.observe(root, { subtree: true, childList: true, characterData: true });
  }

  close(): void { this.watching?.disconnect(); this.watching = null; }

  /** Whether `node` is in this passage's place. */
  holds(node: Node): boolean {
    if ("nodeType" in this.source) return this.source.contains(node);
    try { return this.place.intersectsNode(node); } catch { return false; }
  }

  private reread(): void {
    this.changed = false;
    this.place = asRange(this.source);
    this.flat = flatten(this.place);
    this.spans = alignPieces(this.pieces, tokens(this.flat.text));
    this.ranges.clear();
  }

  /** The sentence `i` on the page; null when the page does not show it. */
  rangeAt(i: number): Range | null {
    const known = this.ranges.get(i);
    if (known && known.startContainer.isConnected && !known.collapsed) return known;
    if (this.changed) this.reread();
    const span = this.spans[i];
    if (!span) return null;
    const a = pointAt(this.flat, span[0], false);
    const b = pointAt(this.flat, span[1], true);
    if (!a || !b) return null;
    const r = this.doc.createRange();
    r.setStart(a.node, a.offset);
    r.setEnd(b.node, b.offset);
    this.ranges.set(i, r);
    return r;
  }

  /** Where piece `i` starts in the whole read, from 0 to 1. */
  share(i: number): number { return this.shares[i] ?? 0; }

  /** The pieces the page shows now, as their first and last index; null when
   *  it shows none. */
  shown(): [number, number] | null {
    if (this.changed) this.reread();
    const first = this.spans.findIndex((s) => s);
    if (first < 0) return null;
    let last = this.spans.length - 1;
    while (!this.spans[last]) last--;
    return [first, last];
  }

  /** A text node the page shows of this passage, for finding what scrolls it. */
  get shownNode(): Node | null {
    const c = this.flat.chunks;
    return c.length ? c[c.length >> 1].node : null;
  }

  /** The sentence a point on the page is in. */
  indexAt(p: Point): number | null {
    if (this.changed) this.reread();
    const off = offsetOf(this.flat, p);
    return off === null ? null : pieceAt(this.spans, off);
  }
}
