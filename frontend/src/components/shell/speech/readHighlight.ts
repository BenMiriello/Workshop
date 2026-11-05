// The sentence being read, marked on the page while highlighting is on (a
// setting, kept: prefs.ts `readHighlight`). It is a CSS custom highlight, so it
// is drawn behind the text by the browser itself: it scrolls with the text,
// is cut by whatever cuts the text and sits under anything drawn over it.
//
// It is LIVE, the one hue that moves: while the voice is going the tint
// breathes, slowly and lightly; paused, it holds. The breathing is a custom
// property animated on the nearest element holding the sentence, which the
// highlight reads, so only that element restyles each frame. Styles go into
// each document a sentence is in, frames included, since a frame's document
// has none of the shell's. A scroll looks again, at most once a frame: an
// editor drawing only the lines near its view replaces them as it scrolls.

import { speechPrefs } from "../../../lib/speech/prefs";
import { watchDocuments } from "./frames";
import { reader } from "./reader";

const READ = "ws-read";
const HOVER = "ws-read-hover";
const ATTR = "data-ws-read";
const STYLE_ID = "ws-read-style";

const CSS_TEXT = `
@property --ws-read-glow { syntax: "<number>"; inherits: true; initial-value: 0; }
::highlight(${READ}) {
  background-color: color-mix(in srgb, var(--live, Highlight) calc(var(--ws-read-glow) * 1%), transparent);
}
::highlight(${HOVER}) { background-color: color-mix(in srgb, var(--fg, CanvasText) 9%, transparent); }
[${ATTR}] { --ws-read-glow: 22; }
[${ATTR}="playing"] { animation: ws-read-glow 2.4s ease-in-out infinite; }
@keyframes ws-read-glow { 0%, 100% { --ws-read-glow: 18; } 50% { --ws-read-glow: 32; } }
@media (prefers-reduced-motion: reduce) { [${ATTR}="playing"] { animation: none; } }
`;

type HighlightWindow = Window & { CSS?: { highlights?: HighlightRegistry }; Highlight?: typeof Highlight };

const drawnIn = new Map<string, Document>();
const drawn = new Map<string, Range | null>();
const registered = new WeakMap<Document, Map<string, Highlight>>();
let glowing: Element | null = null;

/** The one highlight called `name` in `doc`, registered on first use and
 *  never replaced or deleted after: Apple's WebKit leaves a replaced or
 *  deleted highlight painted on the page, while a change to a registered
 *  one's ranges repaints. */
function highlightIn(doc: Document, name: string): Highlight | null {
  const win = doc.defaultView as HighlightWindow | null;
  if (!win?.CSS?.highlights || !win.Highlight) return null;
  let names = registered.get(doc);
  if (!names) { names = new Map(); registered.set(doc, names); }
  let h = names.get(name);
  if (!h) {
    if (!doc.getElementById(STYLE_ID)) {
      const style = doc.createElement("style");
      style.id = STYLE_ID;
      style.textContent = CSS_TEXT;
      doc.head.appendChild(style);
    }
    h = new win.Highlight();
    win.CSS.highlights.set(name, h);
    names.set(name, h);
  }
  return h;
}

function paint(name: string, range: Range | null): void {
  if (drawn.get(name) === range) return;
  const before = drawnIn.get(name);
  if (before) registered.get(before)?.get(name)?.clear();
  drawnIn.delete(name);
  drawn.set(name, range);
  const doc = range?.startContainer.ownerDocument;
  const h = range && doc ? highlightIn(doc, name) : null;
  if (!range || !doc || !h) return;
  h.clear();
  h.add(range);
  drawnIn.set(name, doc);
}

function glow(range: Range | null, phase: string): void {
  const node = range?.commonAncestorContainer ?? null;
  const el = node && (node.nodeType === 1 ? node as Element : node.parentElement);
  if (glowing && glowing !== el) glowing.removeAttribute(ATTR);
  glowing = el;
  el?.setAttribute(ATTR, phase === "paused" ? "paused" : "playing");
}

/** The sentence a tap would read from, under the mouse; null clears it. */
export function showTapTarget(range: Range | null): void {
  paint(HOVER, range);
}

export function highlighting(): boolean {
  return reader.active && speechPrefs.get().readHighlight;
}

/** Keep the mark on the sentence being read. Returns the stop. */
export function watchReadHighlight(): () => void {
  const update = () => {
    const s = reader.getState();
    const range = highlighting() ? reader.passage?.rangeAt(s.index) ?? null : null;
    paint(READ, range);
    glow(range, s.phase);
    if (!highlighting()) paint(HOVER, null);
  };
  const offReader = reader.subscribe(update);
  const offPrefs = speechPrefs.subscribe(update);
  let frame = 0;
  const onScroll = () => {
    if (frame || !highlighting()) return;
    frame = requestAnimationFrame(() => { frame = 0; update(); });
  };
  const offDocs = watchDocuments((doc) => {
    doc.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => doc.removeEventListener("scroll", onScroll, true);
  });
  return () => {
    offReader();
    offPrefs();
    offDocs();
    if (frame) cancelAnimationFrame(frame);
    paint(READ, null);
    paint(HOVER, null);
    glow(null, "idle");
  };
}
