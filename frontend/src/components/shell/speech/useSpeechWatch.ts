// What the speech layer watches in the shell and every same-origin frame
// (frames.ts): which dictatable field is focused, and the text selected, for
// the chip and the Read aloud / Add to chat bar. The speech keys are listened
// for in the same documents.

import { useEffect, useState } from "react";
import type { Box } from "../../../lib/speech/placement";
import { dictationKindOf } from "../../../lib/speech/targets";
import { dictation } from "./dictation";
import { deepActiveElement, documentShown, rangeBox, watchDocuments } from "./frames";
import { onShortcut, onTyping } from "./speechKeys";

export const LAYER = "ws-speech-layer";
// A tap on the chip can blur the field on WebKit before the click lands, so a
// field that loses focus keeps its chip this long.
const BLUR_GRACE_MS = 250;

export type Selected = { text: string; range: Range; box: Box; doc: Document | null; inChat: boolean };

const sameSelection = (a: Selected | null, b: Selected | null) =>
  a === b || (!!a && !!b && a.text === b.text && Math.round(a.box.top) === Math.round(b.box.top)
    && Math.round(a.box.left) === Math.round(b.box.left) && Math.round(a.box.bottom) === Math.round(b.box.bottom));

// By nodeType, not `instanceof Node`: a node in a frame belongs to the frame's
// window and is not an instance of this one's Node.
export const inLayer = (target: EventTarget | Node | null) => {
  const node = target as Node | null;
  if (!node || typeof node.nodeType !== "number") return false;
  const el = node.nodeType === 1 ? node as Element : node.parentElement;
  return !!el?.closest(`.${LAYER}`);
};

export function useSpeechWatch() {
  const [field, setField] = useState<HTMLElement | null>(null);
  const [selected, setSelected] = useState<Selected | null>(null);

  useEffect(() => {
    let focusTimer: ReturnType<typeof setTimeout> | undefined;
    let blurTimer: ReturnType<typeof setTimeout> | undefined;
    let selectTimer: ReturnType<typeof setTimeout> | undefined;
    let frame = 0;
    let selectionDoc: Document | null = null;
    let pressing = false;

    const readFocus = () => {
      const el = deepActiveElement();
      if (!inLayer(el)) dictation.focus(el);
      if (dictationKindOf(el)) {
        clearTimeout(blurTimer);
        setField(el as HTMLElement);
      } else {
        clearTimeout(blurTimer);
        blurTimer = setTimeout(() => setField(null), BLUR_GRACE_MS);
      }
    };
    const readSelection = () => {
      const doc = selectionDoc;
      const sel = doc?.defaultView ? doc.getSelection() : null;
      const text = !pressing && sel && !sel.isCollapsed && sel.rangeCount ? sel.toString().trim() : "";
      if (!doc || !sel || !text || inLayer(sel.anchorNode) || !documentShown(doc)) { setSelected(null); return; }
      const range = sel.getRangeAt(0).cloneRange();
      const box = rangeBox(range);
      if (box.right - box.left === 0 && box.bottom - box.top === 0) { setSelected(null); return; }
      const anchor = sel.anchorNode as Node | null;
      const anchorEl = anchor && (anchor.nodeType === 1 ? anchor as Element : anchor.parentElement);
      const next = { text, range, box, doc, inChat: !!anchorEl?.closest(".ws-beside, .chat-panel") };
      setSelected((prev) => (sameSelection(prev, next) ? prev : next));
    };

    const off = watchDocuments((doc) => {
      const win = doc.defaultView;
      const onFocus = () => {
        clearTimeout(focusTimer);
        focusTimer = setTimeout(readFocus, 0);
      };
      const onSelection = () => {
        selectionDoc = doc;
        clearTimeout(selectTimer);
        selectTimer = setTimeout(readSelection, 200);
      };
      const onScroll = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(readSelection);
      };
      const onDown = (e: PointerEvent) => {
        if (inLayer(e.target)) return;
        dictation.pressed(e.target as Node | null);
        pressing = true;
        setSelected(null);
      };
      const onUp = (e: PointerEvent) => {
        if (inLayer(e.target)) return;
        pressing = false;
        onSelection();
      };
      win?.addEventListener("keydown", onShortcut, true);
      win?.addEventListener("keydown", onTyping, true);
      doc.addEventListener("focusin", onFocus);
      doc.addEventListener("focusout", onFocus);
      doc.addEventListener("selectionchange", onSelection);
      doc.addEventListener("scroll", onScroll, true);
      doc.addEventListener("pointerdown", onDown, true);
      doc.addEventListener("pointerup", onUp, true);
      doc.addEventListener("pointercancel", onUp, true);
      return () => {
        win?.removeEventListener("keydown", onShortcut, true);
        win?.removeEventListener("keydown", onTyping, true);
        doc.removeEventListener("focusin", onFocus);
        doc.removeEventListener("focusout", onFocus);
        doc.removeEventListener("selectionchange", onSelection);
        doc.removeEventListener("scroll", onScroll, true);
        doc.removeEventListener("pointerdown", onDown, true);
        doc.removeEventListener("pointerup", onUp, true);
        doc.removeEventListener("pointercancel", onUp, true);
      };
    });
    readFocus();
    return () => {
      off();
      clearTimeout(focusTimer);
      clearTimeout(blurTimer);
      clearTimeout(selectTimer);
      cancelAnimationFrame(frame);
    };
  }, []);

  return { field, selected, clearSelection: () => setSelected(null) };
}
