// Three-way merge of a document's text, word by word: what two writers did to
// the same starting text, combined. Used when a save finds the document was
// saved elsewhere since the text it was typed on.
//
// A stretch only one side changed takes that side's change. A stretch both
// changed the same way takes it once. Two insertions at the same spot keep
// both, theirs first, since theirs was saved first. Only a stretch both sides
// changed differently is a conflict: mine is kept there by default, and it is
// counted so the caller can say so. Theirs is never lost, it is the version
// already saved. A caller whose "mine" was not typed just now (a copy recovered
// from storage) prefers theirs instead, so a stale copy cannot overwrite newer
// writing.

export interface MergeResult {
  text: string;
  /** Stretches both sides changed differently, where mine was kept. */
  conflicts: number;
}

/** Which side a stretch both sides changed differently takes. */
export type MergePrefer = "mine" | "theirs";

export interface TextChange {
  from: number;
  to: number;
  insert: string;
}

const WORD = /\n|[^\S\n]+|[\p{L}\p{N}_'’]+|[^\s\p{L}\p{N}_'’]/gu;
const LINE = /[^\n]*\n|[^\n]+/g;

/** Past this many differences a word diff gives up, for a line diff or none. */
const MAX_D = 2000;

export const words = (s: string): string[] => s.match(WORD) ?? [];
const lines = (s: string): string[] => s.match(LINE) ?? [];

/** For each token of `a`, the index of the token of `b` it is matched with in
 *  a shortest edit script, or -1. Null when the two differ in more than `maxD`
 *  places. Myers' O(ND) diff, keeping only the live diagonals of each step. */
export function matches(a: string[], b: string[], maxD = MAX_D): Int32Array | null {
  const out = new Int32Array(a.length).fill(-1);
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) { out[pre] = pre; pre++; }
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre
    && a[a.length - 1 - suf] === b[b.length - 1 - suf]) {
    out[a.length - 1 - suf] = b.length - 1 - suf;
    suf++;
  }
  const n = a.length - pre - suf;
  const m = b.length - pre - suf;
  if (n === 0 || m === 0) return out;
  const eq = (x: number, y: number) => a[pre + x] === b[pre + y];

  const trace: Int32Array[] = [];
  let found = -1;
  for (let d = 0; d <= Math.min(n + m, maxD) && found < 0; d++) {
    const prev = trace[d - 1];
    const cur = new Int32Array(2 * d + 1);
    for (let k = -d; k <= d; k += 2) {
      let x: number;
      if (d === 0) x = 0;
      else if (k === -d || (k !== d && prev[k - 1 + d - 1] < prev[k + 1 + d - 1])) x = prev[k + 1 + d - 1];
      else x = prev[k - 1 + d - 1] + 1;
      let y = x - k;
      while (x < n && y < m && eq(x, y)) { x++; y++; }
      cur[k + d] = x;
      if (x >= n && y >= m) { found = d; break; }
    }
    trace.push(cur);
  }
  if (found < 0) return null;

  let x = n;
  let y = m;
  for (let d = found; d > 0; d--) {
    const prev = trace[d - 1];
    const k = x - y;
    const down = k === -d || (k !== d && prev[k - 1 + d - 1] < prev[k + 1 + d - 1]);
    const pk = down ? k + 1 : k - 1;
    const px = prev[pk + d - 1];
    const py = px - pk;
    const sx = down ? px : px + 1;
    const sy = down ? py + 1 : py;
    while (x > sx && y > sy) { x--; y--; out[pre + x] = pre + y; }
    x = px; y = py;
  }
  while (x > 0 && y > 0) { x--; y--; out[pre + x] = pre + y; }
  return out;
}

const same = (x: string[], y: string[]) => x.length === y.length && x.every((t, i) => t === y[i]);

function merge3Tokens(o: string[], a: string[], b: string[], prefer: MergePrefer): MergeResult | null {
  const ma = matches(o, a);
  const mb = matches(o, b);
  if (!ma || !mb) return null;
  const out: string[] = [];
  let conflicts = 0;
  const chunk = (co: string[], ca: string[], cb: string[]) => {
    if (same(ca, co)) out.push(...cb);
    else if (same(cb, co) || same(ca, cb)) out.push(...ca);
    else if (co.length === 0) out.push(...cb, ...ca);
    else { out.push(...(prefer === "mine" ? ca : cb)); conflicts++; }
  };
  let i = 0; let ia = 0; let ib = 0;
  while (i < o.length) {
    let j = 0;
    while (i + j < o.length && ma[i + j] === ia + j && mb[i + j] === ib + j) j++;
    if (j > 0) {
      out.push(...o.slice(i, i + j));
      i += j; ia += j; ib += j;
      continue;
    }
    let q = i;
    while (q < o.length && (ma[q] < 0 || mb[q] < 0)) q++;
    if (q >= o.length) break;
    chunk(o.slice(i, q), a.slice(ia, ma[q]), b.slice(ib, mb[q]));
    i = q; ia = ma[q]; ib = mb[q];
  }
  chunk(o.slice(i), a.slice(ia), b.slice(ib));
  return { text: out.join(""), conflicts };
}

/** `mine` and `theirs`, both written on top of `base`, as one text. */
export function merge3(base: string, mine: string, theirs: string, prefer: MergePrefer = "mine"): MergeResult {
  if (mine === theirs || theirs === base) return { text: mine, conflicts: 0 };
  if (mine === base) return { text: theirs, conflicts: 0 };
  return merge3Tokens(words(base), words(mine), words(theirs), prefer)
    ?? merge3Tokens(lines(base), lines(mine), lines(theirs), prefer)
    ?? { text: prefer === "mine" ? mine : theirs, conflicts: 1 };
}

/** One field both sides may have replaced whole: a title. */
export function mergeField(base: string, mine: string, theirs: string, prefer: MergePrefer = "mine"): MergeResult {
  if (mine === base) return { text: theirs, conflicts: 0 };
  if (theirs === base || theirs === mine) return { text: mine, conflicts: 0 };
  return { text: prefer === "mine" ? mine : theirs, conflicts: 1 };
}

/** Tags as a set: theirs, plus what mine added, less what mine removed. */
export function mergeTags(base: string[], mine: string[], theirs: string[]): string[] {
  const added = mine.filter((t) => !base.includes(t) && !theirs.includes(t));
  return [...theirs.filter((t) => mine.includes(t) || !base.includes(t)), ...added];
}

/** The edits turning `a` into `b`, as offsets into `a`, word by word, so a
 *  caret outside them stays where it was. */
export function changesBetween(a: string, b: string): TextChange[] {
  if (a === b) return [];
  const ta = words(a);
  const tb = words(b);
  const m = matches(ta, tb);
  if (!m) {
    let p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0;
    while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    return [{ from: p, to: a.length - s, insert: b.slice(p, b.length - s) }];
  }
  const out: TextChange[] = [];
  let pos = 0; let i = 0; let j = 0;
  const flush = (upto: number, uptoB: number) => {
    const start = pos;
    let to = pos;
    for (; i < upto; i++) to += ta[i].length;
    const insert = tb.slice(j, uptoB).join("");
    j = uptoB;
    if (to > start || insert) out.push({ from: start, to, insert });
    pos = to;
  };
  for (let k = 0; k < ta.length; k++) {
    if (m[k] < 0) continue;
    flush(k, m[k]);
    pos += ta[k].length; i = k + 1; j = m[k] + 1;
  }
  flush(ta.length, tb.length);
  return out;
}
