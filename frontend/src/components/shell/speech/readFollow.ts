// Follow: the page keeps the sentence being read in view, scrolling when it
// is about to leave it, never on every sentence. It is on while highlighting
// a read that has a place on the page. Scrolling by hand stops it at once, so
// the page never pulls against the reader; while the sentence is out of view
// the player offers Back to reading, which goes there and follows again, and
// scrolling back to the sentence and stopping there follows again too. The
// pattern of Speechify's and Readwise Reader's jump back button.
//
// Only the reader's own hand counts as scrolling: the wheel, a drag on a touch
// screen, a scrolling key, a press on a scroll bar. A page that scrolls itself
// (a chat keeping its newest message in view) does not stop it.
//
// A sentence an editor has not drawn yet (it draws only the lines near its
// view) is scrolled to first (readSeek.ts).

import { speechPrefs } from "../../../lib/speech/prefs";
import { boxOf, frameOffset, watchDocuments } from "./frames";
import { reader } from "./reader";
import { Store } from "./readerStore";
import { offPage, scrollerOf, seek } from "./readSeek";
import { inLayer } from "./useSpeechWatch";

/** `away`: where the sentence being read is while not following, when it is
 *  out of view. */
export type FollowState = { on: boolean; away: "up" | "down" | null };

class Follow extends Store<FollowState> {
  turn(on: boolean): void {
    if (on !== this.state.on) this.set({ on, away: null });
  }
  away(away: FollowState["away"]): void {
    if (away !== this.state.away) this.set({ ...this.state, away });
  }
}

export const follow = new Follow({ on: false, away: null });

// Scrolling stopped this long ago, with the sentence in view: follow again.
const SETTLED_MS = 1000;

const SCROLL_KEYS = new Set(["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "]);
const EDITABLE = "input, textarea, select, [contenteditable]:not([contenteditable=false])";

function current(): Range | null {
  return reader.passage?.rangeAt(reader.getState().index) ?? null;
}

type View = { scroller: Element; top: number; bottom: number; viewTop: number; h: number };

// Where `range` is against the part of its scroller that shows, in the shell
// window's coordinates.
function measure(range: Range): View | null {
  const scroller = scrollerOf(range.startContainer);
  if (!scroller) return null;
  const doc = scroller.ownerDocument;
  const o = frameOffset(range.startContainer.ownerDocument ?? doc);
  const r = range.getBoundingClientRect();
  const whole = scroller === doc.scrollingElement;
  const box = whole ? { top: frameOffset(doc).y, bottom: frameOffset(doc).y + (doc.defaultView?.innerHeight ?? 0) } : boxOf(scroller);
  const viewTop = Math.max(box.top, 0);
  const h = Math.min(box.bottom, window.innerHeight) - viewTop;
  return h > 0 ? { scroller, top: r.top + o.y, bottom: r.bottom + o.y, viewTop, h } : null;
}

/** Bring `range` into the reading band of its scroller: the upper middle of
 *  what shows, clear of the player at the bottom. `now` goes there even when
 *  the sentence is already in sight. */
function reveal(range: Range, now: boolean): void {
  const v = measure(range);
  if (!v) return;
  const { top, bottom, viewTop, h } = v;
  const inBand = top >= viewTop + h * 0.1 && bottom <= viewTop + h * 0.8;
  if (inBand && !now) return;
  const delta = top - (viewTop + h * (bottom - top > h * 0.5 ? 0.1 : 0.3));
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  v.scroller.scrollBy({ top: delta, behavior: still || Math.abs(delta) > h * 2 ? "auto" : "smooth" });
}

// Which way the sentence is, out of view; null when it shows. One an editor
// has not drawn is above or below the lines it has.
function awayOf(range: Range | null): FollowState["away"] {
  const p = reader.passage;
  const i = reader.getState().index;
  const shown = !range && p ? p.shown() : null;
  if (shown) return i < shown[0] ? "up" : i > shown[1] ? "down" : null;
  const v = range && measure(range);
  if (!v) return null;
  if (v.bottom <= v.viewTop) return "up";
  if (v.top >= v.viewTop + v.h) return "down";
  return null;
}

let seeking = 0;

// The sentence being read, put on the page first if it is not drawn yet.
function goTo(i: number, now: boolean): void {
  const p = reader.passage;
  if (!p) return;
  const r = p.rangeAt(i);
  if (r) return reveal(r, now);
  if (!offPage(p, i)) return;
  const mine = ++seeking;
  const still = () => mine === seeking && follow.getState().on && reader.passage === p && reader.getState().index === i;
  void seek(p, i, still).then((found) => { if (found && still()) reveal(found, true); });
}

/** Follow on, and go to the sentence being read now. */
export function goToReading(): void {
  follow.turn(true);
  goTo(reader.getState().index, true);
}

/** Keep following as the read moves on. Returns the stop. */
export function watchFollow(): () => void {
  let passage = reader.passage;
  let index = -1;
  let handAt = 0;
  let settle: ReturnType<typeof setTimeout> | null = null;
  // Not following: say where the sentence is, and follow again once the
  // page has come to rest with it in view.
  const check = () => {
    if (follow.getState().on || !reader.passage) return;
    const away = awayOf(current());
    follow.away(away);
    if (!away && current() && performance.now() - handAt >= SETTLED_MS) follow.turn(true);
  };
  let frame = 0;
  const aim = () => {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; if (!follow.getState().on) follow.away(awayOf(current())); });
  };
  const later = () => {
    if (settle) clearTimeout(settle);
    settle = setTimeout(check, SETTLED_MS);
  };
  const offReader = reader.subscribe(() => {
    if (reader.passage !== passage) {
      passage = reader.passage;
      index = -1;
      follow.turn(!!passage && speechPrefs.get().readHighlight);
    }
    const i = reader.getState().index;
    if (i === index) return;
    index = i;
    if (follow.getState().on) goTo(i, false);
    else check();
  });
  const offPrefs = speechPrefs.subscribe(() => { if (!speechPrefs.get().readHighlight) follow.turn(false); });
  const offDocs = watchDocuments((doc) => {
    const byHand = (e: Event) => {
      if (!reader.passage || inLayer(e.target)) return;
      handAt = performance.now();
      if (follow.getState().on) { seeking++; follow.turn(false); }
      later();
    };
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as Element | null;
      if (SCROLL_KEYS.has(e.key) && !(t?.closest?.(EDITABLE))) byHand(e);
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      const bar = t && t.nodeType === 1 && t.clientWidth > 0
        && (t.scrollHeight > t.clientHeight || t === t.ownerDocument.scrollingElement)
        && (e.offsetX > t.clientWidth || e.offsetY > t.clientHeight);
      if (bar) byHand(e);
    };
    const onScroll = () => { if (!follow.getState().on) { aim(); later(); } };
    doc.addEventListener("wheel", byHand, { capture: true, passive: true });
    doc.addEventListener("touchmove", byHand, { capture: true, passive: true });
    doc.addEventListener("keydown", onKey, true);
    doc.addEventListener("pointerdown", onDown, true);
    doc.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => {
      doc.removeEventListener("wheel", byHand, true);
      doc.removeEventListener("touchmove", byHand, true);
      doc.removeEventListener("keydown", onKey, true);
      doc.removeEventListener("pointerdown", onDown, true);
      doc.removeEventListener("scroll", onScroll, true);
    };
  });
  return () => {
    offReader(); offPrefs(); offDocs();
    if (settle) clearTimeout(settle);
    if (frame) cancelAnimationFrame(frame);
    follow.turn(false);
  };
}
