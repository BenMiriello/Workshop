// The words half of the dictation controller (dictation.ts has the session
// half): where words physically go now, the record of where they went
// (transcript.ts), and taking them back out or moving them.

import { appendPiece } from "../../../lib/speech/insertText";
import { CLIPBOARD, placeKey, type Destination } from "./destination";
import { DictationStore } from "./dictationStore";
import { DictationWords, type Sink } from "./dictationWords";
import { movePiece, type MoveResult } from "./pieceMoves";
import { phraseHistory, type PhraseEntry } from "./phraseHistory";
import { sinkFor } from "./sinkFor";
import { Transcript, type DictationPiece } from "./transcript";

export abstract class DictationParts extends DictationStore {
  protected transcript: Transcript | null = null;
  protected words = this.newWords();

  /** The live session was told the shown words through `to` are settled. */
  protected abstract commit(to: number): void;

  protected newWords(): DictationWords {
    return new DictationWords({
      into: () => this.sink(),
      commit: (to) => this.commit(to),
      report: (open, pending, kept) => {
        const s = this.state;
        if (open !== s.open || pending !== s.pending || kept !== s.kept) this.set({ open, pending, kept });
      },
      placed: (text, state) => {
        const into = this.state.into;
        if (into) this.transcript?.add(text, into, state);
      },
      tookWaiting: () => { if (this.state.into) this.transcript?.mark(this.state.into, "waiting", "placed"); },
      key: () => placeKey(this.state.into ?? CLIPBOARD),
    });
  }

  protected newTranscript(): Transcript {
    const t: Transcript = new Transcript(() => {
      if (this.transcript === t) this.set({ said: t.words, pieces: t.list });
    });
    return t;
  }

  /** Where words physically go now; updates the destination when its field
   *  came back as a new element or was lost. */
  private sink(): Sink {
    const into = this.state.into ?? CLIPBOARD;
    const r = sinkFor(into);
    if (r.into !== into || r.waiting !== this.state.waiting) this.set({ into: r.into, waiting: r.waiting });
    if (r.lost) this.notice({ text: "That field closed. Words go to the clipboard." });
    return r.sink;
  }

  /** Move a piece of this dictation to `d`, out of where it went. */
  async movePiece(p: DictationPiece, d: Destination): Promise<MoveResult> {
    const t = this.transcript;
    return t ? movePiece(t, this.words, p, d) : "failed";
  }

  /** Words sent as a message from `d`: what is said next is a new piece. */
  sentFrom(d: Destination): void { this.transcript?.sent(d); }

  /** Take the last dictated phrase back out; `here`: the last that went
   *  where words go now ("scratch that"), not the last anywhere (Cmd+Z, which
   *  has checked the field first). */
  undo(here = false) {
    return this.edited(() => {
      this.words.freeze();
      const into = this.state.into ?? CLIPBOARD;
      const e = phraseHistory.undo(here ? placeKey(into) : undefined);
      if (e) this.transcript?.dropTail(into, e.text);
      return e;
    });
  }
  redo() { return this.edited(() => phraseHistory.redo()); }
  putBack(e: PhraseEntry) { return this.edited(() => phraseHistory.putBack(e)); }
  takeOut(e: PhraseEntry) { return this.edited(() => { this.words.freeze(); return phraseHistory.takeOut(e); }); }

  // An undo can change what the clipboard holds, which the bar shows.
  private edited<T>(fn: () => T): T {
    const out = fn();
    if (this.busy) this.words.render();
    return out;
  }

  /** Words heard but written nowhere (no command; said in Commands only):
   *  an unplaced piece, copied at Stop and movable from This dictation. */
  keepUnwritten(text: string): void {
    if (!text.trim()) return;
    this.words.clip = appendPiece(this.words.clip, text);
    this.transcript?.cut();
    this.transcript?.add(text, this.state.into ?? CLIPBOARD, "unplaced");
    this.transcript?.cut();
  }

  /** Words to write as if said: a command's own text, or one that did not apply. */
  write(text: string): void { this.words.write(text); }
}
