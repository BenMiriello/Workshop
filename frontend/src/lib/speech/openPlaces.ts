// "Open <name>": which place or page of the shell a spoken name means. Read
// from the places the shell itself lists (components/shell/places.ts), which
// the shell publishes here, so no app is named in this code.
//
// Every place by its name opens where the place opens; every page by its own
// label and by "<place> <page>". A spoken name matches when each of its words
// is one of the target's words (or the start of one, or one letter off), and
// the closest target wins: fewer words left over, then a place over a page,
// then a page of the place the operator is in over the same page elsewhere.

import { landingFor, placeFor, type ShellPlace } from "../../components/shell/places";

export type OpenTarget = { label: string; to: string; words: string[]; rank: number };

let places: ShellPlace[] = [];

export const openPlaces = {
  set(next: ShellPlace[]) { places = next; },
  get: () => places,
};

const FILLERS = new Set(["the", "a", "an", "my", "app", "page", "tab", "to", "please", "up", "over"]);

function wordsOf(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w && !FILLERS.has(w));
}

function oneOff(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  let j = 0;
  let edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++;
    else if (b.length > a.length) j++;
    else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

function wordScore(q: string, words: string[]): number {
  if (words.includes(q)) return 3;
  if (q.length >= 3 && words.some((w) => w.startsWith(q))) return 2;
  if (q.length >= 4 && words.some((w) => oneOff(q, w))) return 1;
  return 0;
}

/** Everything "open" can reach from `pathname`, places first. */
export function openTargets(list: readonly ShellPlace[], pathname: string): OpenTarget[] {
  const here = list.length ? placeFor([...list], pathname) : null;
  const out: OpenTarget[] = list.map((p) => ({ label: p.name, to: landingFor(p), words: wordsOf(p.name), rank: 0 }));
  for (const p of list) {
    for (const page of p.pages) {
      if (wordsOf(page.label).join(" ") === wordsOf(p.name).join(" ")) continue;
      const label = `${p.name} ${page.label}`;
      out.push({ label, to: page.to, words: wordsOf(label), rank: 1 });
      out.push({ label: page.label, to: page.to, words: wordsOf(page.label), rank: p === here ? 1 : 2 });
    }
  }
  return out;
}

/** The target `query` names best, or null when none matches every word. */
export function matchOpen(query: string, targets: readonly OpenTarget[]): OpenTarget | null {
  const q = wordsOf(query);
  if (!q.length) return null;
  let best: OpenTarget | null = null;
  let bestScore = -Infinity;
  for (const t of targets) {
    let s = 0;
    for (const w of q) {
      const got = wordScore(w, t.words);
      if (!got) { s = -Infinity; break; }
      s += got * 10;
    }
    if (s === -Infinity) continue;
    s -= Math.max(0, t.words.length - q.length) * 2 + t.rank;
    if (s > bestScore) { best = t; bestScore = s; }
  }
  return best;
}
