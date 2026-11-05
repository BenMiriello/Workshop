// A numbered list shown for the operator to pick from by voice or click:
// everything that can be done here (Commands), or the commands a
// sentence worded freely most likely meant ("did you mean"). While one is
// showing, or while labels are on the page (labels/pageLabels.ts), the server
// is told that a number said on its own picks something (numbersOn).

import { pageLabels } from "./labels/pageLabels";

/** An item without `run` is an example to read, and has no number. `hint`
 *  is how it is said; `phrases`, every way it can be. */
export type NumberedItem = { n: number | null; label: string; hint?: string; phrases?: string[]; run?: () => void };
export type NumberedGroup = { label: string; items: NumberedItem[] };
export type NumberedList = { kind: "commands" | "choices"; title: string; groups: NumberedGroup[] };

let shown: NumberedList | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

/** Number the items of `groups` that can be run, in order, 1 up. */
export function numberGroups(groups: { label: string; items: Omit<NumberedItem, "n">[] }[]): NumberedGroup[] {
  let n = 0;
  return groups.filter((g) => g.items.length)
    .map((g) => ({ label: g.label, items: g.items.map((i) => ({ ...i, n: i.run ? ++n : null })) }));
}

/** The groups with only the items whose name or phrases have every word of
 *  `q`; numbers stay as they were, so a filtered row is still said by its
 *  number. */
export function filterGroups(groups: NumberedGroup[], q: string): NumberedGroup[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return groups;
  const hit = (i: NumberedItem) => {
    const text = [i.label, i.hint ?? "", ...(i.phrases ?? [])].join(" ").toLowerCase();
    return words.every((w) => text.includes(w));
  };
  return groups.map((g) => ({ ...g, items: g.label.toLowerCase().includes(q.toLowerCase().trim()) ? g.items : g.items.filter(hit) }))
    .filter((g) => g.items.length);
}

export const numbered = {
  subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
  get: () => shown,
  show(list: NumberedList) { shown = list; emit(); },
  close() { if (shown) { shown = null; emit(); } },
  /** Run item `n` of the list showing, closing it first. False when there is none. */
  pick(n: number): boolean {
    const item = shown?.groups.flatMap((g) => g.items).find((i) => i.n === n);
    if (!item?.run) return false;
    shown = null;
    emit();
    item.run();
    return true;
  },
};

export function numbersOn(): boolean {
  return shown !== null || pageLabels.get().on;
}

/** Call `fn` whenever whether numbers are on may have changed. */
export function watchNumbers(fn: (on: boolean) => void): () => void {
  let last = numbersOn();
  const check = () => { const on = numbersOn(); if (on !== last) { last = on; fn(on); } };
  const a = numbered.subscribe(check);
  const b = pageLabels.subscribe(check);
  return () => { a(); b(); };
}
