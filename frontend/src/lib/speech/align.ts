// Where each sentence read aloud sits in the text a page shows.
//
// What is read is cleaned text (markdown without its marks, code blocks left
// out), and what the page shows has more around it: a document's title bar, a
// message's tool cards. And it may show less: an editor puts only the lines
// near its view on the page. So sentences are found by their words, in order:
// first the ones whose words appear exactly once (anchors, kept only where
// they run in reading order), then each other sentence between the anchors
// around it. A sentence whose words are not all there (an image read by its
// description) is pinned by its first and last few words, near where the one
// before it ended; one not found has no place.
//
// Searching on to the end of the page for a sentence's first words is what
// used to put a sentence, and every one after it, at a later spot that
// started the same way.

export type Token = { w: string; start: number; end: number };
export type Span = [number, number];

const WORD = /[\p{L}\p{N}]+(?:['’][\p{L}]+)*/gu;
const EDGE = 3;

export function tokens(text: string): Token[] {
  const out: Token[] = [];
  for (const m of text.matchAll(WORD)) {
    out.push({ w: m[0].toLowerCase().replace(/’/g, "'"), start: m.index, end: m.index + m[0].length });
  }
  return out;
}

function runAt(dom: Token[], at: number, words: string[]): boolean {
  if (at + words.length > dom.length) return false;
  for (let i = 0; i < words.length; i++) if (dom[at + i].w !== words[i]) return false;
  return true;
}

function find(dom: Token[], from: number, to: number, words: string[]): number {
  for (let j = from; j <= Math.min(to, dom.length) - words.length; j++) if (runAt(dom, j, words)) return j;
  return -1;
}

// How far past where the last sentence ended a sentence's first words are
// looked for when its whole words are not there.
const reach = (n: number) => n * 2 + 40;

function count(dom: Token[], words: string[], at: Map<string, number[]>): number[] {
  return (at.get(words[0]) ?? []).filter((j) => runAt(dom, j, words)).slice(0, 2);
}

// The anchors that run in reading order: the longest run of sentences found
// once each whose places increase with them.
function inOrder(found: { i: number; at: number }[]): Set<number> {
  const tails: number[] = [];
  const back: number[] = new Array(found.length).fill(-1);
  found.forEach((f, k) => {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (found[tails[mid]].at < f.at) lo = mid + 1; else hi = mid;
    }
    if (lo > 0) back[k] = tails[lo - 1];
    tails[lo] = k;
  });
  const keep = new Set<number>();
  for (let k = tails.length ? tails[tails.length - 1] : -1; k >= 0; k = back[k]) keep.add(found[k].i);
  return keep;
}

/** Each piece's span of offsets in the page text `dom` was taken from, or
 *  null where it has none. */
export function alignPieces(pieces: string[], dom: Token[]): (Span | null)[] {
  const words = pieces.map((piece) => tokens(piece).map((t) => t.w));
  const at = new Map<string, number[]>();
  dom.forEach((t, j) => { const l = at.get(t.w); if (l) l.push(j); else at.set(t.w, [j]); });
  const once = words.flatMap((w, i) => {
    const hits = w.length >= EDGE ? count(dom, w, at) : [];
    return hits.length === 1 ? [{ i, at: hits[0] }] : [];
  });
  const anchors = inOrder(once);
  const anchorAt = new Map(once.filter((f) => anchors.has(f.i)).map((f) => [f.i, f.at]));
  const nextAnchor: number[] = new Array(words.length).fill(dom.length);
  for (let i = words.length - 2, next = dom.length; i >= 0; i--) {
    next = anchorAt.get(i + 1) ?? next;
    nextAnchor[i] = next;
  }
  let cursor = 0;
  return words.map((w, i) => {
    if (!w.length) return null;
    const limit = nextAnchor[i];
    let first = anchorAt.get(i) ?? find(dom, cursor, limit, w);
    let last = first < 0 ? -1 : first + w.length - 1;
    if (first < 0) {
      first = find(dom, cursor, Math.min(limit, cursor + reach(w.length)), w.slice(0, EDGE));
      if (first < 0) return null;
      const tail = w.slice(-EDGE);
      const end = find(dom, first, Math.min(limit, first + w.length * 2 + 8), tail);
      last = end < 0 ? Math.min(limit, first + w.length) - 1 : end + tail.length - 1;
    }
    cursor = last + 1;
    return [dom[first].start, dom[last].end];
  });
}

/** The piece a point at `offset` in the page text belongs to: the last one
 *  starting at or before it, else the first that has a place. */
export function pieceAt(spans: (Span | null)[], offset: number): number | null {
  let found: number | null = null;
  spans.forEach((s, i) => {
    if (s && s[0] <= offset) found = i;
  });
  if (found !== null) return found;
  const first = spans.findIndex((s) => s);
  return first < 0 ? null : first;
}
