// The speech keys, listened for in the shell and in every same-origin frame:
// the configured shortcuts, Esc while dictating, Cmd/Ctrl+Z on dictated words,
// and typing (which hands the dictated words in that field over to the
// operator).

import { speechPrefs } from "../../../lib/speech/prefs";
import { speechShortcut } from "../../../lib/speech/shortcuts";
import { dictation } from "./dictation";
import { deepActiveElement, selection, speechOptedOut } from "./frames";
import { phraseHistory } from "./phraseHistory";
import { reader } from "./reader";

// A chord is only taken from the page when it does something here, so an
// editor that binds the same keys still gets them otherwise.
export function onShortcut(e: KeyboardEvent) {
  if (onUndo(e)) return;
  const prefs = speechPrefs.get();
  const action = speechShortcut(e, dictation.busy, prefs.shortcuts);
  if (!action) return;
  if (action !== "stop" && speechOptedOut(e.target)) return;
  if (action === "stop" && !escStops()) return;
  if (action === "dictate" && !prefs.dictation) return;
  const sel = action === "read" ? selection() : { text: "", range: null };
  const text = sel.text;
  if (action === "read" && !text && !reader.active) return;
  e.preventDefault();
  e.stopPropagation();
  if (action === "stop") void dictation.stop(true);
  else if (action === "dictate") dictation.toggle(deepActiveElement());
  else if (text) reader.read(text, "selection", sel.range);
  else reader.stop();
}

// Esc is everyone's key. It stops dictation only from the field the words are
// going into, or from nowhere in particular, so closing a dialog or a menu
// while dictating does not end the dictation.
function escStops(): boolean {
  const el = deepActiveElement();
  const into = dictation.getState().into;
  return !el || el === el.ownerDocument.body || (into?.kind === "field" && el === into.target?.el);
}

// In a field whose last change was dictation, Cmd/Ctrl+Z takes the last
// dictated phrase out and Shift puts it back, as the field's own undo would
// have if dictation had typed it. Anywhere else the keys are the page's.
function onUndo(e: KeyboardEvent): boolean {
  if (e.key.toLowerCase() !== "z" || !(e.metaKey || e.ctrlKey) || e.altKey) return false;
  const el = e.target as Element | null;
  if (!el) return false;
  const redo = e.shiftKey;
  if (redo ? !phraseHistory.canRedoIn(el) : !phraseHistory.canUndoIn(el)) return false;
  e.preventDefault();
  e.stopPropagation();
  if (redo) dictation.redo();
  else dictation.undo();
  return true;
}

// Typing in a field makes the dictated words there the operator's.
export function onTyping(e: KeyboardEvent) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key.length === 1 || e.key === "Backspace" || e.key === "Delete" || e.key === "Enter") {
    dictation.typed(e.target as Element | null);
  }
}
