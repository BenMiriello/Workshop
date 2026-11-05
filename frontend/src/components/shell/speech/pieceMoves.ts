// Moving and deleting a piece of the dictation running now: words that went
// to the wrong place are taken out of it and put where they belong.
//
// A piece is taken out only while its place still holds it as dictated (a
// value field, a chat's composer or draft, the words held for the clipboard
// or a field). Once it was edited, sent, or went into a rich editor, it cannot
// be taken out cleanly, so a move leaves it there and copies it: the result
// says which happened.

import { replaceDraftTail } from "../../../lib/draftWire";
import { appendToDraft, draftKeyOf, draftOf } from "../../../lib/speech/chatTargets";
import { appendPiece } from "../../../lib/speech/insertText";
import { withoutPiece, type PieceState } from "../../../lib/speech/pieces";
import { placeKey, type Destination } from "./destination";
import type { DictationWords } from "./dictationWords";
import type { NoticeInput } from "./notices";
import { insertAtTarget } from "./insertAtTarget";
import { writeValue } from "./phraseHistory";
import { sinkFor } from "./sinkFor";
import type { DictationPiece, Transcript } from "./transcript";

type ValueField = HTMLInputElement | HTMLTextAreaElement;

const isValue = (el: Element): el is ValueField => el.tagName === "INPUT" || el.tagName === "TEXTAREA";

/** Whether the piece can still be taken out of its place, and if not why. */
export async function takeOutBlocked(p: DictationPiece): Promise<string | null> {
  if (p.state === "sent") return "It was sent";
  if (p.state !== "placed") return null;
  const sink = sinkFor(p.dest).sink;
  if (sink.kind === "live") {
    const el = sink.target.el;
    if (!isValue(el)) return "That editor keeps its own text";
    return withoutPiece(el.value, p.text) === null ? "It was edited there" : null;
  }
  if (sink.kind === "draft") return (await draftOf(sink.chat)).trimEnd().endsWith(p.text.trim()) ? null : "It was edited there";
  return "Its place is not open";
}

/** Take the piece's words out of where they are; false when they cannot be. */
export async function takeOut(p: DictationPiece, words: DictationWords): Promise<boolean> {
  if (await takeOutBlocked(p)) return false;
  if (p.state === "clipboard" || p.state === "unplaced") {
    const next = withoutPiece(words.clip, p.text);
    if (next === null) return false;
    words.clip = next;
    return true;
  }
  if (p.state === "waiting") {
    const next = withoutPiece(words.waiting, p.text);
    if (next === null) return false;
    words.waiting = next;
    return true;
  }
  const sink = sinkFor(p.dest).sink;
  if (sink.kind === "live" && isValue(sink.target.el)) {
    const f = sink.target.el;
    const value = withoutPiece(f.value, p.text);
    if (value === null) return false;
    writeValue(f, { value, caret: value.length }, "deleteContentBackward");
    return true;
  }
  if (sink.kind === "draft") {
    await replaceDraftTail(draftKeyOf(sink.chat), p.text.trim(), "");
    return true;
  }
  return false;
}

/** Put `text` into `to`; the state it is in there, or null when it could not
 *  go in. */
export function putIn(text: string, to: Destination, words: DictationWords): PieceState | null {
  const sink = sinkFor(to).sink;
  if (sink.kind === "live") return insertAtTarget(sink.target, text) ? "placed" : null;
  if (sink.kind === "draft") { appendToDraft(sink.chat, text); return "placed"; }
  if (sink.kind === "clip") { words.clip = appendPiece(words.clip, text); return "clipboard"; }
  return null;
}

export type MoveResult = "moved" | "copied" | "failed";

export async function movePiece(
  t: Transcript, words: DictationWords, p: DictationPiece, to: Destination,
): Promise<MoveResult> {
  words.freeze();
  t.cut();
  const out = await takeOut(p, words);
  const state = putIn(p.text, to, words);
  if (!state) return "failed";
  if (out) t.moved(p.id, to, state);
  else t.add(p.text, to, state);
  t.cut();
  words.render();
  return out ? "moved" : "copied";
}

/** The newest piece that went to `d` and is still somewhere a move can take
 *  it from, for "Bring last words" after switching places. */
export function lastPieceIn(list: DictationPiece[], d: Destination): DictationPiece | null {
  const key = placeKey(d);
  const p = [...list].reverse().find((x) => x.key === key);
  return p && p.state !== "sent" ? p : null;
}

/** After switching places: where words go now, offering to bring the last
 *  words said to the old place along. */
export function bringNotice(
  d: Destination, move: () => Promise<MoveResult>, notice: (n: NoticeInput) => void,
): NoticeInput {
  return {
    text: `Words go to ${d.name}`,
    action: {
      label: "Bring last words",
      run: () => void move().then((r) => notice({
        text: r === "failed" ? `Could not put the words in ${d.name}` : `${r === "moved" ? "Moved" : "Copied"} to ${d.name}`,
      })),
    },
  };
}
