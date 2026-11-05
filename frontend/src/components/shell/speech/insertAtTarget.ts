// Put dictated text into the field it was meant for.
//
// A focused value field (input, textarea) is written with the `insertText`
// editing command, which the browser treats as typing: the field's own undo
// history keeps it (Ctrl+Z takes the dictation back out) and the page, React
// included, gets a native `input` event. A field that is not focused, or a
// document that refuses the command, is written through the NATIVE value
// setter of the field's own window and sent an `input` event, without taking
// focus from wherever the operator is now; that path resets the field's undo
// history, which is why it is the fallback. Assigning
// `.value` directly would go through React's tracker on a controlled field,
// React would see no change, and the next render would put the old value
// back. Using the prototype setter of the field's own window matters inside
// an app frame, where the shell's HTMLInputElement is a different class.
//
// A field the operator cannot see is usually a hidden helper (a terminal's
// input) that passes each insert on and clears itself, so the last piece sent
// to it is kept, until a key is pressed in it, to space the next one.
//
// A focused contenteditable is written with `insertText`, which an editor sees
// as typing: its own model updates and undo works. One that is not focused
// gets a text node at its kept caret (or its end) and an `input` event, and
// the kept caret moves past it so the next phrase follows.

import { dictationJoin, spliceInsert } from "../../../lib/speech/insertText";
import { dictationKindOf, type DictationKind } from "../../../lib/speech/targets";

export type DictationTarget = {
  el: HTMLElement;
  kind: DictationKind;
  /** Where the caret was in a contenteditable when recording started, kept
   *  because pressing the chip can move a document's selection, and moved
   *  past each insert made while the field is not focused. A value field
   *  keeps its own selection across blur, so it needs none. */
  range: Range | null;
};

export function captureTarget(el: Element | null): DictationTarget | null {
  const kind = dictationKindOf(el);
  if (!el || !kind) return null;
  let range: Range | null = null;
  if (kind === "editable") {
    const sel = el.ownerDocument.getSelection();
    if (sel && sel.rangeCount && el.contains(sel.anchorNode)) range = sel.getRangeAt(0).cloneRange();
  }
  return { el: el as HTMLElement, kind, range };
}

const sentToHelper = new WeakMap<Element, string>();

/** A field the operator cannot see: usually a hidden helper (a terminal's
 *  input) that passes each insert on and clears itself. */
export function unseen(f: HTMLElement): boolean {
  const style = f.ownerDocument.defaultView?.getComputedStyle(f);
  const r = f.getBoundingClientRect();
  if (r.width <= 1 || r.height <= 1) return true;
  return !!style && (style.visibility === "hidden" || Number(style.opacity || "1") <= 0.05);
}

/** Whether the operator is in this field now: it has its document's focus
 *  and that document has the page's. */
export function focused(el: Element): boolean {
  const doc = el.ownerDocument;
  return doc.activeElement === el && doc.hasFocus();
}

function rememberSent(f: HTMLElement, piece: string): void {
  if (!sentToHelper.has(f)) f.addEventListener("keydown", () => sentToHelper.delete(f), { once: true });
  sentToHelper.set(f, piece);
}

function insertIntoValue(f: HTMLInputElement | HTMLTextAreaElement, text: string): void {
  const doc = f.ownerDocument;
  const win = doc.defaultView ?? window;
  let start = f.value.length;
  let end = start;
  try {
    start = f.selectionStart ?? start;
    end = f.selectionEnd ?? start;
  } catch {
    // An input type without a selection API; append.
  }
  const helper = unseen(f);
  const next = spliceInsert(f.value, start, end, text, helper ? sentToHelper.get(f) : "");
  if (!next.piece) return;
  if (helper) rememberSent(f, next.piece);
  if (focused(f) && doc.execCommand("insertText", false, next.piece)) return;
  const proto = f.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(f, next.value);
  else f.value = next.value;
  const place = () => {
    try { f.setSelectionRange(next.caret, next.caret); } catch { /* no selection API */ }
  };
  place();
  f.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertText", data: next.piece }));
  place();
}

// A caret between two nodes (the end of an element, or just after a node this
// file placed) reads its neighbours' text, so spacing still sees the words.
export function textAround(range: Range): { before: string; after: string } {
  const s = range.startContainer;
  const e = range.endContainer;
  const text = (n: Node | null | undefined) => n?.textContent ?? "";
  return {
    before: s.nodeType === Node.TEXT_NODE
      ? text(s).slice(0, range.startOffset) : text(s.childNodes[range.startOffset - 1]),
    after: e.nodeType === Node.TEXT_NODE
      ? text(e).slice(range.endOffset) : text(e.childNodes[range.endOffset]),
  };
}

function placeAfter(range: Range, piece: string, t: DictationTarget): Range {
  range.deleteContents();
  const node = t.el.ownerDocument.createTextNode(piece);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  return range;
}

function insertIntoEditable(t: DictationTarget, text: string): void {
  const doc = t.el.ownerDocument;
  const win = doc.defaultView ?? window;
  const sel = doc.getSelection();
  if (focused(t.el) && sel?.rangeCount && t.el.contains(sel.anchorNode)) {
    const range = sel.getRangeAt(0);
    const { before, after } = textAround(range);
    const piece = dictationJoin(before, after, text);
    if (!piece || doc.execCommand("insertText", false, piece)) return;
    // A document without editing commands: place a text node and tell the page.
    sel.removeAllRanges();
    sel.addRange(placeAfter(range, piece, t));
    t.el.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertText", data: piece }));
    return;
  }
  // Not focused: write where the caret was, or at the end, without taking
  // focus or the selection from wherever the operator is now.
  let range = t.range && t.el.contains(t.range.startContainer) ? t.range : null;
  if (!range) {
    range = doc.createRange();
    range.selectNodeContents(t.el);
    range.collapse(false);
  }
  const { before, after } = textAround(range);
  const piece = dictationJoin(before, after, text);
  if (!piece) return;
  t.range = placeAfter(range, piece, t);
  t.el.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertText", data: piece }));
}

/** Insert `text` at the target's caret, replacing its selection. False when
 *  the field has left the page and there is nowhere to put it. */
export function insertAtTarget(t: DictationTarget, text: string): boolean {
  if (!t.el.isConnected || !t.el.ownerDocument.defaultView) return false;
  if (t.kind === "value") insertIntoValue(t.el as HTMLInputElement, text);
  else insertIntoEditable(t, text);
  return true;
}
