// "The second image", "the last link", "the button at the top right", "delete
// on the third one": a target said by its place among others of its kind, or
// on the screen, rather than by its name. Read over the targets in reading
// order (pageTargets.ts). Pure, so it can be run in tests.

import type { TargetBox } from "./pageTargets";
import type { TargetKind } from "./targetKinds";

export type KindWord = "image" | "link" | "button" | "tab" | "field" | "checkbox" | "item";
export type Region = { x: number | null; y: number | null };
/** `ordinal` counts from 1, or back from the end when negative (-1 is the last). */
export type Place = { ordinal: number | null; kind: KindWord | null; region: Region | null; rest: string };

const ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"];
const SUFFIXED = /^(\d{1,2})(?:st|nd|rd|th)$/;

const KIND_WORDS: Record<string, KindWord> = {
  image: "image", images: "image", picture: "image", pictures: "image", photo: "image",
  photos: "image", thumbnail: "image", video: "image", link: "link", links: "link",
  button: "button", buttons: "button", tab: "tab", tabs: "tab", field: "field", box: "field",
  input: "field", textbox: "field", checkbox: "checkbox", toggle: "checkbox", switch: "checkbox",
  result: "item", results: "item", item: "item", items: "item", one: "item", entry: "item",
  row: "item", option: "item", thing: "item", card: "item", tile: "item",
};

const VERTICAL: Record<string, number> = { top: 0, upper: 0, bottom: 1, lower: 1 };
const HORIZONTAL: Record<string, number> = { left: 0, right: 1 };
const MIDDLE = new Set(["middle", "center", "centre"]);
const FILLER = new Set(["the", "a", "an", "on", "at", "in", "of", "to", "corner", "side", "screen", "page", "s"]);

function ordinalAt(words: string[], i: number): { n: number; used: number } | null {
  const w = words[i];
  const end = (k: number) => words[i + k] === "to" && words[i + k + 1] === "last";
  if (w === "last" || w === "final") return { n: -1, used: 1 };
  if (w === "penultimate") return { n: -2, used: 1 };
  if (w === "next" && end(1)) return { n: -2, used: 3 };
  const at = ORDINALS.indexOf(w);
  const n = at >= 0 ? at + 1 : SUFFIXED.test(w) ? Number(SUFFIXED.exec(w)![1]) : 0;
  if (!n) return null;
  if (end(1)) return { n: -n, used: 3 };
  if (words[i + 1] === "last") return { n: -n, used: 2 };
  return { n, used: 1 };
}

function wordsOf(query: string): string[] {
  return query.toLowerCase().replace(/[-']/g, " ").split(/[^a-z0-9]+/).filter(Boolean);
}

/** The kind of control `query` names, when it names one: "the delete button". */
export function kindWordIn(query: string): KindWord | null {
  for (const w of wordsOf(query)) {
    const k = KIND_WORDS[w];
    if (k && k !== "item") return k;
  }
  return null;
}

/** The place `query` says, or null when it says no ordinal and no region. */
export function parsePlace(query: string): Place | null {
  const words = wordsOf(query);
  let ordinal: number | null = null;
  let kind: KindWord | null = null;
  let x: number | null = null;
  let y: number | null = null;
  let middle = false;
  const rest: string[] = [];
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const o: { n: number; used: number } | null = ordinal === null ? ordinalAt(words, i) : null;
    if (o) { ordinal = o.n; i += o.used - 1; continue; }
    if (w in VERTICAL && y === null) y = VERTICAL[w];
    else if (w in HORIZONTAL && x === null) x = HORIZONTAL[w];
    else if (MIDDLE.has(w)) middle = true;
    else if (w in KIND_WORDS && kind === null) kind = KIND_WORDS[w];
    else if (!FILLER.has(w)) rest.push(w);
  }
  if (middle) { x ??= 0.5; y ??= 0.5; }
  const region = x === null && y === null ? null : { x, y };
  if (ordinal === null && !region) return null;
  return { ordinal, kind, region, rest: rest.join(" ") };
}

type Placed = { box: TargetBox; kind: TargetKind; image: boolean };

/** The word a target's kind is said by in a place ("the third image"), or
 *  null for one no kind word picks out. */
export function kindWordOf(t: Omit<Placed, "box">): KindWord | null {
  if (t.image || t.kind === "image") return "image";
  if (t.kind === "select") return "field";
  if (t.kind === "switch" || t.kind === "radio") return "checkbox";
  return t.kind === "button" || t.kind === "link" || t.kind === "tab" || t.kind === "field" || t.kind === "checkbox"
    ? t.kind : null;
}

/** How a target with no name is said without its number: its place among
 *  those of its kind, while that is a word ("third image"). */
export function placeName(word: KindWord | null, ordinal: number): string {
  return word && ordinal <= ORDINALS.length ? `${ORDINALS[ordinal - 1]} ${word}` : "";
}

export function kindFits(word: KindWord | null, t: Omit<Placed, "box">): boolean {
  switch (word) {
    case null: case "item": return true;
    case "image": return t.image || t.kind === "image";
    case "field": return t.kind === "field" || t.kind === "select";
    case "checkbox": return t.kind === "checkbox" || t.kind === "switch" || t.kind === "radio";
    default: return t.kind === word;
  }
}

type View = { width: number; height: number };

function centre(t: Placed, v: View): { x: number; y: number } {
  return { x: (t.box.left + t.box.width / 2) / v.width, y: (t.box.top + t.box.height / 2) / v.height };
}

// Thirds of the screen: 0 is the top or left third, 1 the bottom or right.
function inBand(at: number, want: number | null): boolean {
  if (want === null) return true;
  return want === 0 ? at < 1 / 3 : want === 1 ? at > 2 / 3 : at >= 1 / 4 && at <= 3 / 4;
}

/** The target `place` means among `targets` (in reading order, of any kind):
 *  those of its kind, inside its region of the screen; by ordinal when one
 *  was said, else the one nearest the region's edge or corner. */
export function pickPlace<T extends Placed>(targets: readonly T[], place: Place, view: View): T | null {
  const r = place.region;
  const fit = targets.filter((t) => {
    if (!kindFits(place.kind, t)) return false;
    if (!r) return true;
    const c = centre(t, view);
    return inBand(c.x, r.x) && inBand(c.y, r.y);
  });
  if (place.ordinal !== null) {
    const i = place.ordinal > 0 ? place.ordinal - 1 : fit.length + place.ordinal;
    return fit[i] ?? null;
  }
  if (!r || !fit.length) return null;
  const off = (t: T) => {
    const c = centre(t, view);
    return (r.x === null ? 0 : Math.abs(c.x - r.x)) + (r.y === null ? 0 : Math.abs(c.y - r.y));
  };
  return fit.reduce((best, t) => (off(t) < off(best) - 0.01 ? t : best));
}
