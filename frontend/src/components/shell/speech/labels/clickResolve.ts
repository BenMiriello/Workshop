// Which control "click <said>" means, in order: a label's number; a place
// among its kind or on the screen said with a kind ("the last link",
// ordinals.ts); the control's name or icon; a place said alone ("the last
// one"); every control of a kind said alone ("click image"); then what the
// words mean, ranked by the server, with a name inside the sentence kept
// when the ranking agrees. Equal fits are not guessed between: they come back
// as choices. The ranking is passed in, so this runs in tests.

import { spansOf } from "../../../../lib/speech/spans";
import { kindFits, kindWordIn, parsePlace, pickPlace, type KindWord, type Place } from "./ordinals";
import type { TargetBox } from "./pageTargets";
import type { TargetKind } from "./targetKinds";
import { nameFit, spokenNumber, wordsOf } from "./targetMatch";

export type Resolvable = {
  n: number; name: string; box: TargetBox; kind: TargetKind; image: boolean; chrome: boolean;
  icon: string; text: string;
};
/** The server's ranking: a score per item and the thresholds that go with it. */
export type Match = { scores: number[] | null; act: number; margin: number; ask: number };
export type Rank = (query: string, items: string[]) => Promise<Match | null>;
export type Resolved<T> = { press: T } | { choose: T[] } | null;
type View = { width: number; height: number };

export const CHOICES_MAX = 9;
const RANKED_CHOICES = 4;

const LEAD = /^(?:(?:please|can|could|would|will|you|i|want|to|like|i'd|let's|just|go|ahead|and)\s+)*(?:click|press|tap|hit|select|choose|open|push|pick)(?:\s+on)?\s+/;
const TAIL = /\s+(?:please|for me|now)$/;

/** The words that name the control, without the asking around them. */
export function clickQuery(said: string): string {
  const s = said.toLowerCase().replace(/[.,!?]+/g, " ").replace(/\s+/g, " ").trim();
  return s.replace(LEAD, "").replace(TAIL, "").trim();
}

const KIND_ONLY: Record<string, KindWord> = {
  image: "image", picture: "image", photo: "image", link: "link", button: "button", tab: "tab", field: "field",
};

type Fit = { score: number; extra: number };
const better = (a: Fit, b: Fit) => a.score > b.score || (a.score === b.score && a.extra < b.extra);

/** The targets `query` fits best by name (closest words, then fewest left
 *  over), every one of them when they tie; with `icons`, by name or icon,
 *  among those showing one; with `exact`, only whole words. */
export function bestByName<T extends Resolvable>(targets: readonly T[], query: string, icons = false, exact = false): T[] {
  let best: T[] = [];
  let key: Fit = { score: -Infinity, extra: Infinity };
  for (const t of targets) {
    if (icons && !t.icon) continue;
    const fit = nameFit(icons ? `${t.name} ${t.icon}` : t.name, query);
    if (!fit || (exact && !fit.exact)) continue;
    if (better(fit, key)) { best = [t]; key = fit; } else if (!better(key, fit)) best.push(t);
  }
  return best;
}

// "The delete button", "the settings icon": the kind said narrows the names
// it is matched against, when any of that kind fit. A name fitting nothing
// may be an icon's: "the pencil".
function byName<T extends Resolvable>(targets: readonly T[], query: string, exact = false): T[] {
  const kind = kindWordIn(query);
  const icons = /\bicons?\b/.test(query);
  if (kind || icons) {
    const narrowed = bestByName(targets.filter((t) => kindFits(kind, t)), query, icons, exact);
    if (narrowed.length) return narrowed;
  }
  const named = bestByName(targets, query, false, exact);
  return named.length ? named : bestByName(targets, query, true, exact);
}

function some<T>(ts: T[]): Resolved<T> {
  if (!ts.length) return null;
  return ts.length === 1 ? { press: ts[0] } : { choose: ts.slice(0, CHOICES_MAX) };
}

const ofKind = <T extends Resolvable>(ts: readonly T[], kind: KindWord | null) => ts.filter((t) => kindFits(kind, t));

async function byMeaning<T extends Resolvable>(pool: readonly T[], query: string, rank: Rank):
  Promise<{ ranked: { t: T; s: number }[]; m: Match } | null> {
  const m = await rank(query, pool.map((t) => t.text)).catch(() => null);
  if (!m?.scores) return null;
  const ranked = pool.map((t, i) => ({ t, s: m.scores![i] ?? -1 })).sort((a, b) => b.s - a.s);
  return { ranked, m };
}

// A sentence about the page's content counts the page, not the shell around
// it, unless nothing there fits.
async function resolvePlace<T extends Resolvable>(targets: readonly T[], place: Place, rank: Rank, view: View): Promise<Resolved<T>> {
  let pool = ofKind(targets, place.kind);
  if (place.rest) {
    const named = byName(pool, place.rest);
    if (named.length) pool = named;
    else {
      const got = await byMeaning(pool.filter(described), place.rest, rank);
      if (!got) return null;
      const top = got.ranked[0]?.s ?? -1;
      const keep = new Set(got.ranked.filter((r) => r.s >= got.m.ask && r.s >= top - got.m.margin).map((r) => r.t));
      pool = pool.filter((t) => keep.has(t));
    }
  }
  const t = pickPlace(pool.filter((x) => !x.chrome), place, view) ?? pickPlace(pool, place, view);
  return t ? { press: t } : null;
}

// A control with neither a name nor an icon says nothing a meaning could
// match, and its short line ("image") would rank high for anything.
const described = (t: Resolvable) => !!(t.name || t.icon);

// A name inside the sentence is kept when the ranking puts it near the top,
// or at the top when it is under half of what was said ("new task" is not
// a link whose name has "task" in it); with no ranking, only when it is not.
async function resolveMeaning<T extends Resolvable>(targets: readonly T[], q: string, rank: Rank): Promise<Resolved<T>> {
  let inside: T[] = [];
  let covers = false;
  for (const span of spansOf(q)) {
    if (spokenNumber(span) !== null) continue;
    inside = byName(targets, span, true);
    if (inside.length) { covers = wordsOf(span).length * 2 >= wordsOf(q).length; break; }
  }
  const got = await byMeaning(targets.filter(described), q, rank);
  if (!got || !got.ranked.length) return covers ? some(inside) : null;
  const { ranked, m } = got;
  const top = ranked[0];
  const slack = covers ? m.margin / 2 : 0;
  const agreed = inside.filter((t) => (ranked.find((r) => r.t === t)?.s ?? -1) >= top.s - slack);
  if (agreed.length) return some(agreed);
  const twins = ranked.filter((r) => r.t.text === top.t.text);
  const next = ranked.find((r) => r.t.text !== top.t.text);
  if (top.s >= m.act && top.s - (next?.s ?? -1) >= m.margin) return some(twins.map((r) => r.t));
  if (top.s < m.ask) return null;
  return { choose: ranked.filter((r) => r.s >= m.ask).slice(0, RANKED_CHOICES).map((r) => r.t) };
}

export async function resolveClick<T extends Resolvable>(targets: readonly T[], said: string, rank: Rank, view: View): Promise<Resolved<T>> {
  const q = clickQuery(said);
  if (!q) return null;
  const num = spokenNumber(q);
  if (num !== null) return some(targets.filter((x) => x.n === num));
  const place = parsePlace(q);
  if (place && (place.kind || place.region)) return resolvePlace(targets, place, rank, view);
  const named = byName(targets, q);
  if (named.length) return some(named);
  if (place) return resolvePlace(targets, place, rank, view);
  const kind = KIND_ONLY[q.replace(/^(?:the|a|an)\s+/, "").replace(/s$/, "")];
  if (kind) {
    const all = ofKind(targets, kind);
    const page = all.filter((t) => !t.chrome);
    return some(page.length ? page : all);
  }
  return resolveMeaning(targets, q, rank);
}
