// Undo for dictation: the phrases dictation placed, newest last, each able to
// take itself back out and put itself back.
//
// Live dictation writes outside the browser's own undo history, so Cmd/Ctrl+Z
// in a field whose last change was dictation is answered here (speechKeys.ts),
// as is "scratch that". Placing new words clears the redo side, as any undo
// does; typing in a field drops that field's phrases, and its keys are its own
// again.

import { appendDraft, replaceDraftTail } from "../../../lib/draftWire";
import { draftKeyOf, type ChatTarget } from "../../../lib/speech/chatTargets";
import { appendPiece } from "../../../lib/speech/insertText";
import { dropLast, putBack, takeOut, type Placed } from "../../../lib/speech/phraseUndo";

export type PhraseEntry = {
  /** The start of the phrase, for the bar's notice. */
  text: string;
  /** The field it is in, when it is in one. */
  el: Element | null;
  undo: () => boolean;
  redo: () => boolean;
  /** The place it went to (destination.ts placeKey). */
  key?: string;
};

type ValueField = HTMLInputElement | HTMLTextAreaElement;
const KEEP = 50;

export function writeValue(f: ValueField, next: { value: string; caret: number }, inputType: string): void {
  const win = f.ownerDocument.defaultView ?? window;
  const proto = f.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(f, next.value);
  else f.value = next.value;
  f.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType }));
  try { f.setSelectionRange(next.caret, next.caret); } catch { /* no selection API */ }
}

/** A phrase held as text rather than in a field (the clipboard's words, or a
 *  field's that has left the page), read and written through `get`/`set`. */
export function heldEntry(get: () => string, set: (v: string) => void, text: string): PhraseEntry {
  return {
    text: text.trim(),
    el: null,
    undo: () => {
      const next = dropLast(get(), text);
      const ok = next !== get().trimEnd();
      set(next);
      return ok;
    },
    redo: () => { set(appendPiece(get(), text)); return true; },
  };
}

/** A phrase that went onto the draft of a chat not on screen. The draft is
 *  rewritten only while it still ends with the phrase. */
export function draftEntry(chat: ChatTarget, text: string): PhraseEntry {
  const key = () => draftKeyOf(chat);
  return {
    text: text.trim(),
    el: null,
    undo: () => { void replaceDraftTail(key(), text.trim(), "").catch(() => {}); return true; },
    redo: () => { void appendDraft(key(), text).catch(() => {}); return true; },
  };
}

/** A phrase placed in a value field. */
export function valueEntry(f: ValueField, p: Placed): PhraseEntry {
  return {
    text: p.text.trim(),
    el: f,
    undo: () => {
      const next = takeOut(f.value, p);
      if (next) writeValue(f, next, "deleteContentBackward");
      return !!next;
    },
    redo: () => {
      const next = putBack(f.value, p);
      if (next) writeValue(f, next, "insertText");
      return !!next;
    },
  };
}

class PhraseHistory {
  private done: PhraseEntry[] = [];
  private undone: PhraseEntry[] = [];

  placed(e: PhraseEntry): void {
    this.done = [...this.done, e].slice(-KEEP);
    this.undone = [];
  }

  /** The operator typed in `el`: its phrases are theirs now. */
  typedIn(el: Element | null): void {
    if (!el) return;
    this.done = this.done.filter((e) => e.el !== el);
    this.undone = this.undone.filter((e) => e.el !== el);
  }

  /** Whether the last thing dictation did in `el` can be undone or redone. */
  canUndoIn(el: Element): boolean {
    return this.done[this.done.length - 1]?.el === el;
  }

  canRedoIn(el: Element): boolean {
    return this.undone[this.undone.length - 1]?.el === el;
  }

  /** Take the last phrase back out, or with `key` the last one that went to
   *  that place. The entry, or null when there was none or its field no
   *  longer holds it (then it is forgotten). */
  undo(key?: string): PhraseEntry | null {
    const i = key === undefined ? this.done.length - 1 : this.done.map((x) => x.key).lastIndexOf(key);
    const e = i >= 0 ? this.done[i] : undefined;
    if (!e) return null;
    this.done = this.done.filter((x) => x !== e);
    if (!e.undo()) return null;
    this.undone.push(e);
    return e;
  }

  redo(): PhraseEntry | null {
    const e = this.undone.pop();
    if (!e) return null;
    if (!e.redo()) return null;
    this.done.push(e);
    return e;
  }

  /** Whether `e` is in its field ("in"), taken out ("out"), or forgotten
   *  (typed over, or too old). */
  standing(e: PhraseEntry): "in" | "out" | null {
    return this.done.includes(e) ? "in" : this.undone.includes(e) ? "out" : null;
  }

  /** Put `e` back, wherever it is in the history. */
  putBack(e: PhraseEntry): boolean {
    if (!this.undone.includes(e) || !e.redo()) return false;
    this.undone = this.undone.filter((x) => x !== e);
    this.done.push(e);
    return true;
  }

  /** Take `e` out again, wherever it is in the history. */
  takeOut(e: PhraseEntry): boolean {
    if (!this.done.includes(e) || !e.undo()) return false;
    this.done = this.done.filter((x) => x !== e);
    this.undone.push(e);
    return true;
  }
}

export const phraseHistory = new PhraseHistory();
