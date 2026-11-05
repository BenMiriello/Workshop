// While something is being read with highlighting on, a tap on text reads
// from the sentence tapped: in the text being read it moves the voice there;
// in another message, document or part of the page it starts reading that
// (readPlaces.ts says which text). Paused or not, it plays from there, and it
// turns Follow back on, since the reader is looking at that spot. With a
// mouse, the sentence a click would read from is shaded under the pointer.
//
// A tap is a press and release in about the same spot. A drag, a long press
// (selecting text), a press on a control or in a field is left to the page.

import { readPieces } from "../../../lib/speech/sentences";
import { watchDocuments } from "./frames";
import { Passage, type Point } from "./passage";
import { follow } from "./readFollow";
import { highlighting, showTapTarget } from "./readHighlight";
import { readableAt, textAt } from "./readPlaces";
import { reader } from "./reader";
import { inLayer } from "./useSpeechWatch";

const MOVE_PX = 8;
const TAP_MS = 600;
const HOVER_KEEP_MS = 3000;
const CONTROL = [
  "a[href]", "button", "input", "textarea", "select", "label", "summary",
  "[role=button]", "[role=link]", "[role=menuitem]", "[role=tab]", "[role=switch]", "[role=checkbox]",
  "[contenteditable]:not([contenteditable=false])", '[data-read-aloud="off"]',
].join(",");

const onControl = (target: EventTarget | null) => {
  const el = target as Element | null;
  return !!el && el.nodeType === 1 && !!el.closest(CONTROL);
};

/** The passage a point would be read in, without starting it: the read's
 *  own, or one laid over the place it belongs to (kept a few seconds, so a
 *  moving pointer does not lay it again on every move). */
const hovered = new WeakMap<Element, { passage: Passage | null; at: number }>();
function passageFor(p: Point): Passage | null {
  if (reader.passage?.holds(p.node)) return reader.passage;
  const r = readableAt(p.node);
  if (!r) return null;
  const known = hovered.get(r.el);
  if (known && performance.now() - known.at < HOVER_KEEP_MS) return known.passage;
  const passage = Passage.over(r.el, readPieces(r.text()));
  hovered.set(r.el, { passage, at: performance.now() });
  return passage;
}

function readFrom(p: Point): void {
  const own = reader.passage;
  const i = own?.holds(p.node) ? own.indexAt(p) : null;
  if (i !== null) reader.jump(i);
  else {
    const r = readableAt(p.node);
    if (!r) return;
    reader.read(r.text(), r.source, r.el, p);
  }
  follow.turn(true);
}

/** Listen for taps on text in every document. Returns the stop. */
export function watchReadTap(): () => void {
  return watchDocuments((doc) => {
    let down: { x: number; y: number; t: number } | null = null;
    let frame = 0;
    const onDown = (e: PointerEvent) => {
      const plain = e.isPrimary && e.button === 0 && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey;
      down = plain && highlighting() ? { x: e.clientX, y: e.clientY, t: e.timeStamp } : null;
    };
    const onUp = (e: PointerEvent) => {
      const d = down;
      down = null;
      if (!d || !highlighting() || inLayer(e.target) || onControl(e.target)) return;
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > MOVE_PX || e.timeStamp - d.t > TAP_MS) return;
      if (doc.getSelection()?.isCollapsed === false) return;
      const p = textAt(doc, e.clientX, e.clientY);
      if (p) readFrom(p);
    };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const p = highlighting() && !onControl(e.target) ? textAt(doc, e.clientX, e.clientY) : null;
        const passage = p && passageFor(p);
        const i = p && passage ? passage.indexAt(p) : null;
        const target = passage && i !== null ? passage.rangeAt(i) : null;
        const reading = target && passage === reader.passage && i === reader.getState().index;
        showTapTarget(reading ? null : target);
      });
    };
    const onLeave = () => { cancelAnimationFrame(frame); showTapTarget(null); };
    doc.addEventListener("pointerdown", onDown, true);
    doc.addEventListener("pointerup", onUp, true);
    doc.addEventListener("pointermove", onMove, { capture: true, passive: true });
    // A frame's document may have no root yet while it loads.
    const root = doc.documentElement;
    root?.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      doc.removeEventListener("pointerdown", onDown, true);
      doc.removeEventListener("pointerup", onUp, true);
      doc.removeEventListener("pointermove", onMove, true);
      root?.removeEventListener("pointerleave", onLeave);
    };
  });
}
