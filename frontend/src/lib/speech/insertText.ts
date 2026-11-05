// Where dictated words go in a field's text, as arithmetic over strings.
//
// The DOM half (which element, which setter, which event) is in
// components/shell/speech/insertAtTarget.ts. This half decides only what the
// field's value becomes and where the caret lands, so it can be tested.
//
// Spacing is the one judgement made here. Dictation arrives as a clean phrase
// ("Hello there."), and dropping it between two words with no space glues it to
// both. So a space is added on a side where the neighbour is a word character
// and none is there already, and never before punctuation the phrase starts
// with, or after an opening bracket or quote the caret sits behind.

const OPENS = /[([{"'`/\-]/;
const CLOSES = /^[.,;:!?)\]}%]/;

/** `text` without spaces at either end. Line breaks stay: they are words too
 *  ("new line", "new paragraph"), and one at the end is the newest thing said. */
export function spaceTrim(text: string): string {
  return text.replace(/^[ \t]+|[ \t]+$/g, "");
}

/** Two pieces of dictation one after the other: one space between them,
 *  none where a line break already separates them. */
export function joinSaid(a: string, b: string): string {
  const x = spaceTrim(a);
  const y = spaceTrim(b);
  if (!x || !y) return x || y;
  return x.endsWith("\n") || y.startsWith("\n") ? x + y : `${x} ${y}`;
}

/** The text to insert between `before` and `after`, with spacing added. */
export function dictationJoin(before: string, after: string, text: string): string {
  let piece = spaceTrim(text);
  if (!piece) return "";
  const prev = before.slice(-1);
  const next = after.slice(0, 1);
  if (prev && !/\s/.test(prev) && !OPENS.test(prev) && !CLOSES.test(piece) && !piece.startsWith("\n")) {
    piece = " " + piece;
  }
  if (next && !/\s/.test(next) && !CLOSES.test(next) && !piece.endsWith("\n")) piece = piece + " ";
  return piece;
}

/** Replace `value[start, end)` with the dictated text. Returns the new value,
 *  the caret position just after what was inserted, and the inserted piece
 *  with its spacing. Offsets out of range or reversed are clamped and ordered,
 *  because a field's selection can be stale by the time a transcript comes
 *  back. `sentBefore` stands in for the text before the caret when the field
 *  has none: a terminal's hidden input clears itself after passing text on, so
 *  what it was last sent is the only record of what the caret follows. */
export function spliceInsert(
  value: string, start: number, end: number, text: string, sentBefore = "",
): { value: string; caret: number; piece: string } {
  const clamp = (n: number) => Math.max(0, Math.min(value.length, Number.isFinite(n) ? n : value.length));
  const s = clamp(Math.min(start, end));
  const e = clamp(Math.max(start, end));
  const piece = dictationJoin(value.slice(0, s) || sentBefore, value.slice(e), text);
  return { value: value.slice(0, s) + piece + value.slice(e), caret: s + piece.length, piece };
}

/** `text` with `piece` added after it, one space between (none across a
 *  line break). */
export function appendPiece(text: string, piece: string): string {
  if (!spaceTrim(piece)) return text;
  return joinSaid(text, piece);
}
