// The words still being said, written into the field they are going into and
// rewritten there as they are revised, the way phone and desktop dictation do.
//
// A region is a run of text this file wrote and still owns. Before each
// rewrite it checks that the field still holds exactly that text where it was
// left, with the caret right after it when the field is focused. Anything else
// means the operator typed, moved the caret, sent or cleared the field: the
// words there are theirs now, `show` answers false, and the caller lets the
// region go (and tells the server, so they are never typed again).
//
// Value fields (input, textarea) are rewritten with `setRangeText` and sent an
// `input` event, which a React controlled field reads as a change because the
// value moved without its tracked setter. A focused contenteditable is
// rewritten by selecting the region back from the caret and inserting over it
// with the editing command, so an editor sees typing. One that is not focused
// gets a text node of its own.

import { dictationJoin, joinSaid, spaceTrim } from "../../../lib/speech/insertText";
import type { Placed } from "../../../lib/speech/phraseUndo";
import { revealCaret } from "./fieldRow";
import { focused, textAround, type DictationTarget } from "./insertAtTarget";

type ValueField = HTMLInputElement | HTMLTextAreaElement;

const same = (a: string, b: string) => a.replace(/ /g, " ") === b.replace(/ /g, " ");

// How much of the region a rewrite leaves as it is: only the rest is replaced,
// so a word that did not change is not written again.
function kept(was: string, now: string): number {
  let n = 0;
  while (n < was.length && n < now.length && was[n] === now[n]) n++;
  return n;
}

function announce(el: HTMLElement, data: string): void {
  const win = el.ownerDocument.defaultView ?? window;
  el.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data }));
}

export class LiveRegion {
  /** What the field holds of this region now, spacing included. */
  private piece = "";
  private start = -1;
  private node: Text | null = null;

  constructor(readonly target: DictationTarget) {}

  get text(): string {
    return spaceTrim(this.piece);
  }

  /** Rewrite the region as `text`. False when the field changed under it. */
  show(text: string): boolean {
    const el = this.target.el;
    if (!el.isConnected || !el.ownerDocument.defaultView) return false;
    return this.target.kind === "value" ? this.showValue(el as ValueField, text) : this.showEditable(text);
  }

  /** Where a value field holds the region now, for undo; null for an editor,
   *  which keeps its own. */
  get placed(): Placed | null {
    return this.target.kind === "value" && this.start >= 0 && this.piece ? { start: this.start, text: this.piece } : null;
  }

  /** `text` is settled: it is written, and the region carries on after it,
   *  showing `rest`. `onPlaced` hears where the settled part is in a value
   *  field. */
  settle(text: string, rest: string, onPlaced?: (p: Placed) => void): boolean {
    if (!this.show(joinSaid(text, rest))) return false;
    const lead = this.piece.length - this.piece.replace(/^[ \t]+/, "").length;
    const cut = spaceTrim(text) ? lead + spaceTrim(text).length : 0;
    if (this.start >= 0 && cut > 0 && this.target.kind === "value") {
      onPlaced?.({ start: this.start, text: this.piece.slice(0, cut) });
    }
    if (this.start >= 0) this.start += cut;
    if (this.node && cut > 0 && cut < this.node.data.length) this.node = this.node.splitText(cut);
    else if (this.node && cut > 0) this.node = null;
    this.piece = this.piece.slice(cut);
    return true;
  }

  private showValue(f: ValueField, text: string): boolean {
    let end: number;
    if (this.start < 0) {
      let s = f.value.length;
      let e = s;
      try {
        s = f.selectionStart ?? s;
        e = f.selectionEnd ?? s;
      } catch { /* an input type without a selection API: append */ }
      this.start = Math.min(s, e);
      end = Math.max(s, e);
    } else {
      end = this.start + this.piece.length;
      if (f.value.slice(this.start, end) !== this.piece) return false;
      if (focused(f) && (f.selectionStart !== end || f.selectionEnd !== end)) return false;
    }
    const piece = dictationJoin(f.value.slice(0, this.start), f.value.slice(end), text);
    if (piece === this.piece) return true;
    const keep = kept(this.piece, piece);
    try {
      f.setRangeText(piece.slice(keep), this.start + keep, end, "end");
    } catch {
      const win = f.ownerDocument.defaultView ?? window;
      const proto = f.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
      const next = f.value.slice(0, this.start) + piece + f.value.slice(end);
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(f, next);
      else f.value = next;
    }
    this.piece = piece;
    announce(f, piece);
    // After the field has handled the change (a field that sizes itself to
    // its text does it in that event), and again once it has laid out.
    revealCaret(f);
    (f.ownerDocument.defaultView ?? window).requestAnimationFrame(() => revealCaret(f));
    return true;
  }

  private showEditable(text: string): boolean {
    const el = this.target.el;
    const doc = el.ownerDocument;
    const sel = doc.getSelection();
    const inside = focused(el) && !!sel?.rangeCount && el.contains(sel.anchorNode);
    if (!inside || this.node) return this.showNode(text);
    if (this.piece) {
      if (!sel!.isCollapsed) return false;
      for (let i = 0; i < this.piece.length; i++) sel!.modify("extend", "backward", "character");
      if (!same(sel!.toString(), this.piece)) {
        sel!.collapseToEnd();
        return false;
      }
    }
    const { before, after } = textAround(sel!.getRangeAt(0));
    const piece = dictationJoin(before, after, text);
    const keep = kept(this.piece, piece);
    sel!.collapseToEnd();
    if (piece === this.piece) return true;
    for (let i = keep; i < this.piece.length; i++) sel!.modify("extend", "backward", "character");
    if (piece.length > keep) doc.execCommand("insertText", false, piece.slice(keep));
    else if (this.piece.length > keep) doc.execCommand("delete");
    this.piece = piece;
    return true;
  }

  // Not focused: a text node of the region's own, at the kept caret or the end.
  private showNode(text: string): boolean {
    const el = this.target.el;
    if (this.node) {
      if (!this.node.isConnected || !same(this.node.data, this.piece)) return false;
      const range = el.ownerDocument.createRange();
      range.selectNode(this.node);
      const { before, after } = textAround(range);
      this.piece = dictationJoin(before, after, text);
      this.node.data = this.piece;
      announce(el, this.piece);
      return true;
    }
    if (this.piece) return false;
    let range = this.target.range && el.contains(this.target.range.startContainer) ? this.target.range : null;
    if (!range) {
      range = el.ownerDocument.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
    }
    const { before, after } = textAround(range);
    const piece = dictationJoin(before, after, text);
    if (!piece) return true;
    range.deleteContents();
    this.node = el.ownerDocument.createTextNode(piece);
    range.insertNode(this.node);
    range.setStartAfter(this.node);
    range.collapse(true);
    this.target.range = range;
    this.piece = piece;
    announce(el, piece);
    return true;
  }
}
