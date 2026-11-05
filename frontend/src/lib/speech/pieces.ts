// One dictation as PIECES: each run of words that went to one place, in order.
// The transcript is the record; what a field or chat holds is a copy of it, so
// a piece can still be found, copied or moved after it went somewhere wrong.
//
// A new piece starts when the place changes, when a message is sent, or when
// the operator types in the place. Pauses and time passing never start one, so
// a long stretch into one field is one piece and each sent message is its own.

import { joinSaid } from "./insertText";
import type { PlaceRef } from "./dictationsApi";

/** placed: in its field or chat; waiting: held for a field that left the page;
 *  clipboard: the clipboard is its place; unplaced: went nowhere (its field
 *  was given up on), copied at Stop; sent: sent as a chat message. */
export type PieceState = "placed" | "waiting" | "clipboard" | "unplaced" | "sent";

export type Piece<D> = {
  id: number;
  /** Which place it went to (destination.ts placeKey). */
  key: string;
  place: PlaceRef;
  dest: D;
  text: string;
  state: PieceState;
  at: number;
};

export class Pieces<D> {
  list: Piece<D>[] = [];
  private open = false;
  private nextId = 1;

  /** Settled words went to `key`. They join the last piece when it is still
   *  open and went to the same place in the same state. */
  add(text: string, key: string, place: PlaceRef, dest: D, state: PieceState, now = Date.now()): Piece<D> | null {
    if (!text.trim()) return null;
    const last = this.list[this.list.length - 1];
    if (last && this.open && last.key === key && last.state === state) {
      last.text = joinSaid(last.text, text);
      return last;
    }
    const p: Piece<D> = { id: this.nextId++, key, place, dest, text: joinSaid("", text), state, at: now };
    this.list = [...this.list, p];
    this.open = true;
    return p;
  }

  /** What is said next starts a new piece. */
  cut(): void { this.open = false; }

  /** The open piece for `key` was sent; the next words start a new message. */
  sent(key: string): void {
    const last = this.list[this.list.length - 1];
    if (last && this.open && last.key === key && last.state === "placed") last.state = "sent";
    this.open = false;
  }

  /** Every piece for `key` in state `from` is in state `to` now. */
  mark(key: string, from: PieceState, to: PieceState): boolean {
    let changed = false;
    for (const p of this.list) if (p.key === key && p.state === from) { p.state = to; changed = true; }
    return changed;
  }

  /** The newest phrase of the newest piece for `key` was taken back out. */
  dropTail(key: string, phrase: string): void {
    const p = [...this.list].reverse().find((x) => x.key === key);
    if (!p) return;
    const t = p.text.trimEnd();
    const s = phrase.trim();
    if (s && t.endsWith(s)) p.text = t.slice(0, t.length - s.length).trimEnd();
  }

  update(id: number, patch: Partial<Pick<Piece<D>, "key" | "place" | "dest" | "text" | "state">>): void {
    this.list = this.list.map((p) => (p.id === id ? { ...p, ...patch } : p));
    if (this.list[this.list.length - 1]?.id === id) this.open = false;
  }

  remove(id: number): void {
    this.list = this.list.filter((p) => p.id !== id);
    this.open = false;
  }

  get words(): string {
    return this.list.reduce((all, p) => joinSaid(all, p.text), "");
  }

  /** Words that are in no place of their own: copied at Stop. */
  get loose(): string {
    return this.list.filter((p) => p.state === "unplaced" || p.state === "waiting")
      .reduce((all, p) => joinSaid(all, p.text), "");
  }
}

/** `value` with the last copy of `text` in it taken out, with the space that
 *  joined it; null when the value no longer holds it as dictated. */
export function withoutPiece(value: string, text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  const at = value.lastIndexOf(t);
  if (at < 0) return null;
  let start = at;
  let end = at + t.length;
  if (start > 0 && value[start - 1] === " ") start--;
  else if (value[end] === " ") end++;
  return value.slice(0, start) + value.slice(end);
}
