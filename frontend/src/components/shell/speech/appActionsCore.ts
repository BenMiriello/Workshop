// The pure rules behind the actions pages and apps declare by voice
// (appActions.ts): how one is said, what the server is told about it, and how
// the words said with it become its argument.
//
// A phrase holds at most one slot standing for the input ("add to {group}").
// A slot at the end, or a phrase with none, is followed by the input's words,
// which the server sends as the argument. A slot elsewhere needs choices, and
// is said as each choice in place ("put sunsets first"): the server is told
// every such wording with the choice it means. More slots than one, or a free
// text slot before the end, and the phrase is not used.

import type { SpokenAction } from "@workshop/ui";
import type { LiveAction } from "../../../lib/speech/liveWire";

// Wordings made from a phrase and each choice, at most.
const MAX_FIXED = 200;
// Of those, learned from for loose phrasing.
const MAX_FILLED = 24;
const SLOT = /\{[^{}]*\}/g;

type Phrase = { before: string; after: string; slot: boolean };

function parse(p: string): Phrase | null {
  const t = p.trim().replace(/\s+/g, " ");
  const slots = t.match(SLOT) ?? [];
  if (slots.length > 1 || !t) return null;
  if (!slots.length) return { before: t, after: "", slot: false };
  const [before, after] = t.split(SLOT).map((x) => x.trim());
  return { before, after, slot: true };
}

/** The ways to say `a`, parsed; its title when it declares none. */
function phrases(a: SpokenAction): Phrase[] {
  const own = (a.phrases ?? []).map(parse).filter((p): p is Phrase => !!p && !!(p.before || p.after));
  const usable = own.filter((p) => !p.slot || !!a.input);
  return usable.length ? usable : [{ before: a.title.trim().toLowerCase(), after: "", slot: false }];
}

const fill = (p: Phrase, v: string) => [p.before, v, p.after].filter(Boolean).join(" ");

/** How Commands shows the first way to say `a`: its slot as `‹name›`. */
export function shownPhrase(a: SpokenAction): string {
  const p = phrases(a)[0];
  return a.input ? fill(p, `‹${a.input.name}›`) : p.before;
}

/** What the live socket is told about `a`, under `id`. */
export function wireOf(id: string, a: SpokenAction): LiveAction {
  const list = phrases(a);
  const choices = a.input?.choices ?? [];
  const open = a.input ? list.filter((p) => !p.after && p.before).map((p) => p.before) : [];
  const exact = a.input ? [] : list.map((p) => p.before);
  const fixed: [string, string][] = [];
  for (const p of list) for (const c of choices) if (fixed.length < MAX_FIXED) fixed.push([fill(p, c), c]);
  const own = (a.examples ?? []).flatMap((e) => {
    const p = parse(e);
    if (!p) return [];
    return p.slot ? choices.slice(0, 3).map((c) => fill(p, c)) : [e.trim()];
  });
  return {
    id, title: a.title, description: a.description, phrases: [...exact, ...open], fixed,
    examples: [...own, ...fixed.slice(0, MAX_FILLED).map(([w]) => w)], takesArg: !!a.input,
  };
}

const words = (t: string) => t.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
const same = (a: string, b: string) => a === b || `${a}s` === b || `${b}s` === a;
const has = (list: string[], w: string) => list.some((x) => same(x, w));

/** The choice `said` names: every word of the choice in what was said ("add
 *  this to the sunsets group"), or everything said inside the choice
 *  ("sunset" for "Sunsets 2024"). The fullest wins. */
export function pickChoice(said: string, choices: readonly string[]): string | null {
  const heard = words(said);
  let best: string | null = null;
  let bestScore = 0;
  for (const c of choices) {
    const cw = words(c);
    if (!cw.length || !heard.length) continue;
    const score = cw.every((w) => has(heard, w)) ? cw.length * 2
      : heard.every((w) => has(cw, w)) ? heard.length : 0;
    if (score > bestScore) { best = c; bestScore = score; }
  }
  return best;
}

/** The words after one of `a`'s phrases, when a sentence worded freely
 *  starts with it; null when it does not. */
export function afterPhrase(sentence: string, a: SpokenAction): string | null {
  const said = sentence.trim().split(/\s+/);
  const heard = said.map((w) => words(w).join(""));
  for (const p of phrases(a)) {
    const pw = words(p.before);
    if (p.after || !pw.length || pw.length >= heard.length) continue;
    if (pw.every((w, i) => heard[i] === w)) return said.slice(pw.length).join(" ").replace(/[.,!?;:]+$/, "") || null;
  }
  return null;
}

export type Arg = { ok: true; arg: string | undefined } | { ok: false; missing: string };

/** What `a` runs with, from the words said for its input: the choice they
 *  name, or the words themselves. */
export function argFor(a: SpokenAction, said: string | null): Arg {
  const choices = a.input?.choices;
  if (!a.input || !said) return { ok: true, arg: undefined };
  if (!choices?.length) return { ok: true, arg: said };
  const pick = pickChoice(said, choices);
  return pick ? { ok: true, arg: pick } : { ok: false, missing: `No ${a.input.name} "${said}"` };
}

/** Whether `a` asks before it runs. */
export const asksFirst = (a: SpokenAction) => a.annotations?.consequentialHint === true;
