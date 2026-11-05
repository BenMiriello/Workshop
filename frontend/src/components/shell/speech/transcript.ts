// The record of one dictation: its pieces (lib/speech/pieces.ts), kept on the
// server as they are said and told to the apps when the dictation ends.
//
// Saved while it runs, a moment after each change, so a tab closed or crashed
// mid-dictation loses nothing: the server keeps the last day of dictations and
// Settings > Speech lists them.

import {
  newDictationId, postDictation, putDictation, type PieceBody, type PlaceRef,
} from "../../../lib/speech/dictationsApi";
import { Pieces, type Piece, type PieceState } from "../../../lib/speech/pieces";
import { placeKey, type Destination } from "./destination";

const SAVE_AFTER_MS = 1500;

export type DictationPiece = Piece<Destination>;

export function placeRef(d: Destination): PlaceRef {
  if (d.kind === "chat") {
    const t = d.chat.threadId;
    return { kind: "chat", name: d.name, path: t ? `/chat/${encodeURIComponent(d.chat.scope)}?thread=${t}` : "" };
  }
  if (d.kind === "field") return { kind: "field", name: d.name, path: d.href };
  return { kind: "clipboard", name: d.name, path: "" };
}

const bodyOf = (p: DictationPiece): PieceBody => ({
  place: p.place, text: p.text, state: p.state, at: new Date(p.at).toISOString(),
});

export class Transcript {
  readonly id = newDictationId();
  readonly pieces = new Pieces<Destination>();
  private readonly startedAt = new Date();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  constructor(private changed: () => void) {}

  get words(): string { return this.pieces.words; }
  get list(): DictationPiece[] { return this.pieces.list; }

  add(text: string, d: Destination, state: PieceState): void {
    if (this.pieces.add(text, placeKey(d), placeRef(d), d, state)) this.touched();
  }

  /** The next words start a new piece. */
  cut(): void { this.pieces.cut(); }

  sent(d: Destination): void { this.pieces.sent(placeKey(d)); this.touched(); }

  mark(d: Destination, from: PieceState, to: PieceState): void {
    if (this.pieces.mark(placeKey(d), from, to)) this.touched();
  }

  dropTail(d: Destination, phrase: string): void { this.pieces.dropTail(placeKey(d), phrase); this.touched(); }

  /** The piece is in `d` now, in `state`. */
  moved(id: number, d: Destination, state: PieceState): void {
    this.pieces.update(id, { key: placeKey(d), place: placeRef(d), dest: d, state });
    this.touched();
  }

  remove(id: number): void { this.pieces.remove(id); this.touched(); }

  private touched(): void {
    this.changed();
    if (this.closed || this.timer) return;
    this.timer = setTimeout(() => { this.timer = null; this.save(); }, SAVE_AFTER_MS);
  }

  private save(): void {
    if (this.closed || !this.list.length) return;
    void putDictation(this.id, {
      started_at: this.startedAt.toISOString(), pieces: this.list.map(bodyOf),
    }).catch(() => {});
  }

  /** Tell the server it ended; best effort, never awaited by anything on
   *  screen. A phrase scratched out of the clipboard is gone from its piece. */
  close(status: "done" | "cancelled"): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    this.closed = true;
    const text = this.words;
    if (!text) return;
    const places: PlaceRef[] = [];
    for (const p of this.list) {
      const last = places[places.length - 1];
      if (!last || last.kind !== p.place.kind || last.name !== p.place.name) places.push(p.place);
    }
    void postDictation({
      id: this.id, text, places, status, pieces: this.list.map(bodyOf),
      started_at: this.startedAt.toISOString(), ended_at: new Date().toISOString(),
    }).catch(() => {});
  }
}
