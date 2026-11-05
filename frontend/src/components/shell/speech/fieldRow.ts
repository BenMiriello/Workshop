// Where a text field's text ends, for the dictation chip (lib/speech/placement.ts,
// rowSpot): just past its last character, on which line, and how tall a line
// is. Read only; the field itself is never changed.
//
// A textarea is measured with a hidden copy laid out with the same width and
// text styles (the way caret-position libraries do it), an input by measuring
// its text, and a contenteditable from the box of its last character.

import type { Box, TextEnd } from "../../../lib/speech/placement";
import { rangeBox } from "./frames";

const px = (v: string) => Number.parseFloat(v) || 0;

// What decides where a textarea's text wraps and how tall its lines are.
const COPIED = [
  "boxSizing", "width", "borderLeftWidth", "borderRightWidth", "borderTopWidth", "borderBottomWidth",
  "paddingLeft", "paddingRight", "paddingTop", "paddingBottom",
  "fontStyle", "fontVariant", "fontWeight", "fontStretch", "fontSize", "fontFamily", "lineHeight",
  "letterSpacing", "wordSpacing", "textTransform", "textIndent", "tabSize", "direction",
] as const;

function lineHeightOf(style: CSSStyleDeclaration): number {
  const font = px(style.fontSize) || 16;
  return style.lineHeight === "normal" ? font * 1.2 : px(style.lineHeight) || font * 1.2;
}

// Where the end of a textarea's text (or of its first `upto` characters)
// falls, relative to its border box.
function areaEnd(el: HTMLTextAreaElement, style: CSSStyleDeclaration, upto?: number): { x: number; y: number } {
  const doc = el.ownerDocument;
  const copy = doc.createElement("div");
  const s = copy.style;
  for (const k of COPIED) s[k] = style[k];
  s.position = "absolute";
  s.visibility = "hidden";
  s.top = "0";
  s.left = "-9999px";
  // An empty field shows its placeholder, which may be kept to one line.
  const hint = el.value ? null : doc.defaultView?.getComputedStyle(el, "::placeholder");
  s.whiteSpace = hint?.whiteSpace === "nowrap" || hint?.whiteSpace === "pre" ? "pre" : "pre-wrap";
  s.overflowWrap = "break-word";
  s.overflow = "hidden";
  s.borderStyle = "solid";
  // A scrollbar narrows the text; the copy has none, so take its width off.
  const scrollbar = el.offsetWidth - el.clientWidth - px(style.borderLeftWidth) - px(style.borderRightWidth);
  if (scrollbar > 0) s.width = `${el.offsetWidth - scrollbar}px`;
  copy.textContent = upto === undefined ? el.value || el.placeholder : el.value.slice(0, upto);
  const mark = doc.createElement("span");
  mark.textContent = "​";
  copy.appendChild(mark);
  doc.body.appendChild(copy);
  const at = { x: mark.offsetLeft - el.scrollLeft, y: mark.offsetTop - el.scrollTop };
  copy.remove();
  return at;
}

// Where the end of an input's text falls, relative to its border box.
let ruler: CanvasRenderingContext2D | null = null;
function inputEnd(el: HTMLInputElement, style: CSSStyleDeclaration): number {
  ruler ??= document.createElement("canvas").getContext("2d");
  if (!ruler) return px(style.borderLeftWidth) + px(style.paddingLeft);
  ruler.font = style.font || `${style.fontSize} ${style.fontFamily}`;
  const text = el.value || el.placeholder || "";
  return px(style.borderLeftWidth) + px(style.paddingLeft) + ruler.measureText(text).width - el.scrollLeft;
}

// The box of an editable's last character, in shell coordinates.
function editableEnd(el: HTMLElement): Box | null {
  const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let last: Text | null = null;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if ((n.textContent ?? "").trim()) last = n as Text;
  }
  if (!last) return null;
  const text = last.data.replace(/\s+$/, "");
  const range = el.ownerDocument.createRange();
  range.setStart(last, Math.max(0, text.length - 1));
  range.setEnd(last, text.length);
  const rects = range.getClientRects();
  if (!rects.length) return null;
  const r = rangeBox(range);
  return r.right > r.left || r.bottom > r.top ? r : null;
}

// The block the caret is in: the nearest element inside `el` laid out as a
// block, or `el` itself.
function blockOf(node: Node, el: HTMLElement): Element {
  for (let e = node.nodeType === 1 ? node as Element : node.parentElement; e && e !== el; e = e.parentElement) {
    const d = e.ownerDocument.defaultView?.getComputedStyle(e).display ?? "";
    if (!d.startsWith("inline")) return e;
  }
  return el;
}

// The caret's line in an editable: just past its last character, and its
// centre, in shell coordinates. Null when the caret is not in it.
function editableCaret(el: HTMLElement, line: number): (Row & { near: Row[] }) | null {
  const sel = el.ownerDocument.getSelection();
  const node = sel?.rangeCount ? sel.focusNode : null;
  if (!sel || !node || !el.contains(node)) return null;
  const at = sel.getRangeAt(0).cloneRange();
  at.collapse(false);
  const caret = at.getClientRects()[0] ?? (node.nodeType === 1 ? (node as Element) : node.parentElement)?.getBoundingClientRect();
  if (!caret) return null;
  const middle = (caret.top + caret.bottom) / 2;
  const range = el.ownerDocument.createRange();
  range.selectNodeContents(blockOf(node, el));
  let right = caret.right;
  for (const r of range.getClientRects()) {
    if (r.width && Math.abs((r.top + r.bottom) / 2 - middle) < line / 2) right = Math.max(right, r.right);
  }
  const o = rangeBox(at);
  const dx = o.left - at.getBoundingClientRect().left;
  const dy = o.top - at.getBoundingClientRect().top;
  const near = editableLines(el, middle).map((l) => ({ x: l.x + dx, rowCenter: l.rowCenter + dy }));
  return { x: right + dx, rowCenter: middle + dy, near };
}

type Row = { x: number; rowCenter: number };

// The ends of an editable's lines in sight, nearest `middle` first: each
// block's last line, where a paragraph's text stops (a blank one at its
// start), in the editable's own document's coordinates.
function editableLines(el: HTMLElement, middle: number): Row[] {
  const view = el.ownerDocument.defaultView;
  const out: Row[] = [];
  const range = el.ownerDocument.createRange();
  for (const line of el.children) {
    const box = line.getBoundingClientRect();
    if (box.bottom < 0 || box.top > (view?.innerHeight ?? 0)) continue;
    range.selectNodeContents(line);
    const rects = [...range.getClientRects()].filter((r) => r.width);
    const last = rects[rects.length - 1];
    out.push(last
      ? { x: last.right, rowCenter: (last.top + last.bottom) / 2 }
      : { x: box.left + px(view?.getComputedStyle(line).paddingLeft ?? ""), rowCenter: (box.top + box.bottom) / 2 });
  }
  return out.sort((a, b) => Math.abs(a.rowCenter - middle) - Math.abs(b.rowCenter - middle)).slice(0, 12);
}

// The caret's line in a textarea, relative to its border box.
function areaCaret(el: HTMLTextAreaElement, style: CSSStyleDeclaration, innerRight: number, box: Box, line: number) {
  let at: number;
  try { at = el.selectionEnd; } catch { return null; }
  const caret = areaEnd(el, style, at);
  const nl = el.value.indexOf("\n", at);
  const end = areaEnd(el, style, nl < 0 ? el.value.length : nl);
  const x = end.y === caret.y ? box.left + end.x : innerRight;
  return { x, rowCenter: box.top + caret.y + line / 2 };
}

/** Where the field's text ends, in the coordinates of `box` (the field's box
 *  in the shell window). */
export function textEnd(el: HTMLElement, box: Box): TextEnd | null {
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  if (!style) return null;
  const line = lineHeightOf(style);
  const borderL = px(style.borderLeftWidth);
  const borderR = px(style.borderRightWidth);
  const scrollbar = Math.max(0, el.offsetWidth - el.clientWidth - borderL - borderR);
  const innerRight = box.right - borderR - scrollbar;
  const contentLeft = box.left + borderL + px(style.paddingLeft);
  const contentTop = box.top + px(style.borderTopWidth) + px(style.paddingTop);
  if (el.tagName === "INPUT") {
    const hint = !(el as HTMLInputElement).value;
    return { x: box.left + inputEnd(el as HTMLInputElement, style), rowCenter: (box.top + box.bottom) / 2, lineHeight: line, innerRight, hint };
  }
  if (el.tagName === "TEXTAREA") {
    const at = areaEnd(el as HTMLTextAreaElement, style);
    const hint = !(el as HTMLTextAreaElement).value;
    const caret = areaCaret(el as HTMLTextAreaElement, style, innerRight, box, line);
    return { x: box.left + at.x, rowCenter: box.top + at.y + line / 2, lineHeight: line, innerRight, hint, caret };
  }
  const last = editableEnd(el);
  if (!last) return { x: contentLeft, rowCenter: contentTop + line / 2, lineHeight: line, innerRight };
  const caret = editableCaret(el, line);
  return { x: last.right, rowCenter: (last.top + last.bottom) / 2, lineHeight: line, innerRight, caret, near: caret?.near };
}

/** Scroll a value field so its caret is in view, as typing does; dictated
 *  words are written without moving the field. At the end of the text it
 *  scrolls to the bottom, so a row added under the text shows too. */
export function revealCaret(el: HTMLInputElement | HTMLTextAreaElement): void {
  let at = el.value.length;
  try { at = el.selectionEnd ?? at; } catch { /* an input type without a selection API */ }
  const end = at >= el.value.length;
  if (el.tagName !== "TEXTAREA") {
    if (end) el.scrollLeft = el.scrollWidth;
    return;
  }
  if (end) { el.scrollTop = el.scrollHeight; return; }
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  if (!style) return;
  const line = lineHeightOf(style);
  const top = areaEnd(el as HTMLTextAreaElement, style, at).y + el.scrollTop;
  if (top < el.scrollTop) el.scrollTop = top;
  else if (top + line > el.scrollTop + el.clientHeight) el.scrollTop = top + line - el.clientHeight;
}
