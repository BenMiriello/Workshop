// Taking a dictated phrase back out of a field's text, and putting it back.
//
// Live dictation writes a field outside the browser's own undo history, so
// the speech layer keeps its own short one: each phrase it placed, where. A
// phrase is only taken out when the field still holds exactly it there; once
// the text around it has changed, the record no longer describes the field.

export type Placed = { start: number; text: string };

/** `value` without the phrase, and where the caret goes; null when the field
 *  no longer holds the phrase where it was placed. */
export function takeOut(value: string, p: Placed): { value: string; caret: number } | null {
  if (!p.text || value.slice(p.start, p.start + p.text.length) !== p.text) return null;
  return { value: value.slice(0, p.start) + value.slice(p.start + p.text.length), caret: p.start };
}

/** `value` with the phrase back where it was; null when that is past the end. */
export function putBack(value: string, p: Placed): { value: string; caret: number } | null {
  if (p.start > value.length) return null;
  return { value: value.slice(0, p.start) + p.text + value.slice(p.start), caret: p.start + p.text.length };
}

/** A clipboard dictation's text without its last phrase. */
export function dropLast(text: string, last: string): string {
  const t = text.trimEnd();
  const l = last.trim();
  return l && t.endsWith(l) ? t.slice(0, t.length - l.length).trimEnd() : t;
}

/** A placed piece as its sentences, each where it is, so taking back the last
 *  phrase takes back one sentence, not everything that settled together. */
export function sentencesOf(p: Placed): Placed[] {
  const out: Placed[] = [];
  let start = p.start;
  for (const piece of p.text.split(/(?<=[.!?])(?=\s)/)) {
    if (piece.trim()) out.push({ start, text: piece });
    start += piece.length;
  }
  return out;
}
