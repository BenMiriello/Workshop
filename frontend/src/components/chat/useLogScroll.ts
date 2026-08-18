import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  FOLLOW_SLOP, restoresToBottom, type ScrollPos as ScrollPosRule,
} from "./scrollRestore";
import { userStorage } from "../../lib/storage";

// Following the conversation: when the log sticks to the bottom, and when it
// must not.
//
// This is one behaviour spread across three refs, two pieces of state and five
// effects, and almost every rule in it exists because breaking it produced a
// specific, reported bug. Keeping them together is the point: they only make
// sense as a set, and each was previously separated from the others by hundreds
// of lines of unrelated chat code.
//
// THE RULES:
//
//  - `atBottom` has exactly ONE writer: the scroll handler. A render-driven
//    effect used to re-assert it, which destroyed the position tracking and
//    defeated every guard below.
//  - Following is conditional on already following. This fires on every streamed
//    token; unguarded it hard-scrolled the log down on every token, so scrolling
//    up to read an earlier message while a turn streamed was impossible.
//  - Leaving the bottom is not a trap. New content while scrolled up raises a
//    jump-to-latest affordance instead of yanking the view.
//  - Focus does NOT scroll. It used to, unconditionally: tapping the composer to
//    start typing threw you to the newest message, discarding wherever you had
//    scrolled to. Reading something further up and then replying to it is an
//    ordinary thing to do, and focusing an input is not a request to go
//    anywhere.
//  - Sending DOES scroll. It is an explicit intent to follow again.
//  - The composer shares the column with the log, so every row it grows by is a
//    row the log loses. Nothing else notices that resize — it is not a render, a
//    viewport change, or a scroll — so an observer on the element closes the
//    gap where the bottom message slid underneath the composer.
//  - Autosize runs in a LAYOUT effect. The scroll effects also fire on `input`,
//    and passive effects run in declaration order, so resizing passively meant
//    every scroll was computed against the composer's PREVIOUS height and landed
//    short by exactly the row that had just been added.
//  - Returning to a chat RESTORES where you left it. Each thread's position is
//    saved (per id, in sessionStorage so it survives a reload and a thread
//    switch) and reapplied when that thread is opened. If nothing is saved, or
//    you were at the bottom, it lands at the bottom. The catch the naive version
//    always hit: the transcript keeps resizing AFTER first paint — the clamps and
//    their ResizeObservers settle a frame or two later — so a single scroll on
//    load computed against the pre-settle height and stopped a page short of the
//    bottom. The restore therefore re-applies its target across a few frames
//    until the scroll height stops changing, so late reflow can't leave the view
//    parked above where it belongs.

const SCROLL_STORE_PREFIX = "ws-chat-scroll:";
// The two thresholds and the rule that picks between them live in their own
// module, pure and tested. See scrollRestore.ts for why they are two numbers.
type ScrollPos = ScrollPosRule;

function loadScrollPos(key: string): ScrollPos | null {
  try {
    const raw = userStorage.session.getItem(SCROLL_STORE_PREFIX + key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (typeof v?.top === "number" && typeof v?.atBottom === "boolean") return v;
  } catch { /* private mode / bad JSON — fall back to bottom */ }
  return null;
}

// COALESCED TO ONE WRITE PER FRAME. `scroll` fires continuously while a finger
// is moving, and `sessionStorage.setItem` is synchronous main-thread I/O plus a
// `JSON.stringify` — so the act of scrolling a long transcript on a phone was
// paying for a serialize-and-store on every event it emitted. Only the last
// position in a burst is worth keeping, so the rest is dropped rather than
// written and immediately overwritten.
//
// The deferral is safe because it is bounded by a frame AND explicitly flushed
// before the page can go away: requestAnimationFrame does not run while the
// document is hidden, so a position saved just before backgrounding would
// otherwise sit in memory and be lost.
let pendingScroll: { key: string; pos: ScrollPos } | null = null;
let scrollFrame = 0;

function writeScrollPos(key: string, pos: ScrollPos): void {
  userStorage.session.setItem(SCROLL_STORE_PREFIX + key, JSON.stringify(pos));
}

function flushScrollPos(): void {
  if (scrollFrame) { cancelAnimationFrame(scrollFrame); scrollFrame = 0; }
  const p = pendingScroll;
  pendingScroll = null;
  if (p) writeScrollPos(p.key, p.pos);
}

function saveScrollPos(key: string, pos: ScrollPos): void {
  pendingScroll = { key, pos };
  if (scrollFrame) return;
  scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; flushScrollPos(); });
}

export type LogScroll = {
  scrollRef: React.RefObject<HTMLDivElement>;
  composerRef: React.RefObject<HTMLDivElement>;
  inputRef: React.RefObject<HTMLTextAreaElement>;
  showJumpToLatest: boolean;
  onLogScroll: () => void;
  // Return to the bottom and resume following. Used by send and by the
  // jump-to-latest button.
  followLatest: () => void;
  // Call synchronously, BEFORE triggering a fetch that will PREPEND older
  // content above the top of the log. See the `oldestSeq` param below for how
  // the other half of the pair fires once that content has actually painted.
  holdForOlder: () => void;
};

export function useLogScroll({
  input,
  inputFocused,
  // Anything whose arrival should keep a following log pinned to the bottom.
  followDeps,
  // True when there is any content at all, so "new content while scrolled up"
  // can raise the affordance.
  hasContent,
  // Identity of the open thread. Its scroll position is saved under this key and
  // restored when the same thread is opened again. null disables restore (there
  // is nothing stable to key it on), and it still lands at the bottom.
  restoreKey,
  // The composer has been expanded to a fixed tall box. Autosize stands down
  // while it is: the two would fight over the same inline height, and the
  // operator's chosen size must win over the content's.
  expanded = false,
  // The seq of the oldest block currently on screen. Watched, not read: a
  // change in it (and only a change in it — see the effect below) is what
  // says "older content just landed above the fold", which is the one moment
  // `holdForOlder`'s captured geometry needs to be applied.
  oldestSeq = null,
}: {
  input: string;
  inputFocused: boolean;
  followDeps: unknown[];
  hasContent: boolean;
  restoreKey?: string | null;
  expanded?: boolean;
  oldestSeq?: number | null;
}): LogScroll {
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Whether the log is scrolled to (near) the bottom, so focus/keyboard/viewport
  // changes only re-pin when the operator was already following.
  const atBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  // The thread whose position we are currently persisting, and the restore's
  // moving parts. `restoringRef` is true while a per-thread restore is settling;
  // during it the follow effect stands down so the two don't fight over the
  // position. `restoredForRef` guards "restore once per thread": a thread's saved
  // spot is reapplied when it opens, not on every streamed token after.
  const restoreKeyRef = useRef<string | null>(restoreKey ?? null);
  const restoringRef = useRef(false);
  const restoredForRef = useRef<string | null>(null);
  const targetRef = useRef<ScrollPos | null>(null);

  // Instant by default — the auto-follow paths below fire on every streamed
  // token, and animating each of those fights itself. `smooth` is for the
  // explicit, one-off jumps (the Jump-to-latest button, sending a message):
  // an operator-triggered scroll should read as scrolling, not teleporting.
  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Land the last coalesced position before the page can stop running frames,
  // and on the way out of this thread.
  useEffect(() => {
    const flush = () => flushScrollPos();
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", flush);
    return () => {
      flush();
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", flush);
    };
  }, []);

  // The ONLY writer of atBottomRef.
  const onLogScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distance < FOLLOW_SLOP;
    atBottomRef.current = atBottom;
    // Remember where this thread is left. Skipped while a restore is settling —
    // those are our own programmatic scrolls converging on the target, not the
    // operator moving the view, and saving them would just rewrite the target
    // with itself frame by frame.
    if (!restoringRef.current && restoreKeyRef.current) {
      saveScrollPos(restoreKeyRef.current, { top: el.scrollTop, atBottom });
    }
    if (atBottom) { setShowJumpToLatest(false); return; }
    // More than a full window scrolled up: show it regardless of whether
    // anything new has arrived — "how far back am I" needs no new message to
    // be worth answering. Short of that, leave whatever the new-content
    // effect below decided; scrolling within a window of the bottom doesn't
    // by itself mean the newest message is out of view.
    const farUp = distance > el.clientHeight;
    if (farUp) setShowJumpToLatest(true);
  }, []);

  // Called at SEND TIME, before the message that triggered it has reached the
  // store: `onSend` fires synchronously ahead of the dispatch that adds the
  // pending bubble, because the dispatch is what the send pipeline is for and
  // this hook cannot reorder it. Scrolling immediately therefore targets
  // `scrollHeight` as it stood the instant BEFORE that bubble exists — a
  // smooth scroll's target is a fixed pixel offset, not a moving one, so the
  // animation lands short of the true bottom once the bubble (and the
  // composer shrinking back down) grow the log underneath it. The `scroll`
  // event that fires as it settles then measures a real gap and marks
  // `atBottomRef` false, which is the opposite of what this function is
  // trying to say and disables the follow effect that would otherwise have
  // corrected it — the log stops one message short and offers "jump to
  // latest" instead of just being there. Reported 2026-08-18.
  //
  // A rAF is enough: React's commit for the dispatch that follows this call
  // (and any composer-height change riding with it) lands within the same
  // frame or the next, well inside the wait, and the deferred read gets the
  // real post-layout height instead of a stale one.
  const followLatest = useCallback(() => {
    atBottomRef.current = true;
    setShowJumpToLatest(false);
    requestAnimationFrame(() => { if (atBottomRef.current) scrollToBottom(true); });
  }, [scrollToBottom]);

  // PRESERVE READING POSITION WHEN OLDER CONTENT IS PREPENDED. The browser
  // keeps `scrollTop` fixed in PIXELS when something is inserted above the
  // viewport, which shoves whatever the operator was reading further down the
  // screen — the classic infinite-scroll-upward jump. `holdForOlder` captures
  // the geometry right before the fetch that will grow the log upward; the
  // layout effect below applies the correction once that content has actually
  // painted (a real DOM commit, keyed on `oldestSeq` changing — not a fixed
  // delay, which would race the render).
  const olderHoldRef = useRef<{ top: number; height: number } | null>(null);
  const holdForOlder = useCallback(() => {
    const el = scrollRef.current;
    if (el) olderHoldRef.current = { top: el.scrollTop, height: el.scrollHeight };
  }, []);
  useLayoutEffect(() => {
    const hold = olderHoldRef.current;
    olderHoldRef.current = null;
    const el = scrollRef.current;
    if (!hold || !el) return;
    // The content grew by exactly this many pixels above where the operator
    // was; shifting scrollTop by the same amount keeps the same pixel of the
    // same message under the same point on screen, rather than re-deriving a
    // "where was I" from geometry that has already changed underneath it.
    el.scrollTop = hold.top + (el.scrollHeight - hold.height);
    // Not a follow: the operator was reading further up, at rest, and this
    // does not touch atBottomRef or the jump-to-latest affordance either way.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oldestSeq]);

  // Drive the view to the restore target and HOLD it there while the transcript
  // finishes reflowing. The clamps and their observers change heights for a frame
  // or two after first paint, so a one-shot scroll lands against a stale height —
  // this reapplies the target each frame until the scroll height goes quiet (or a
  // short cap elapses, so a chat that never settles can't loop forever). A null
  // target, or one marked atBottom, means the bottom; scrollHeight is read fresh
  // each frame so "the bottom" tracks the growing content.
  const settleRestore = useCallback(() => {
    restoringRef.current = true;
    let lastH = -1;
    let stableFrames = 0;
    const start = performance.now();
    // Decided ONCE, from the saved position, not re-derived per frame: the
    // half-viewport test reads clientHeight and scrollHeight, and scrollHeight is
    // exactly what is still moving during a restore. Sampling it mid-settle would
    // let the answer flip frame to frame.
    const el0 = scrollRef.current;
    const toBottom = !el0
      || restoresToBottom(targetRef.current, el0.scrollHeight, el0.clientHeight);
    const step = () => {
      const el = scrollRef.current;
      if (!el) { restoringRef.current = false; return; }
      const t = targetRef.current;
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = toBottom || !t ? max : Math.min(t.top, max);
      const h = el.scrollHeight;
      stableFrames = h === lastH ? stableFrames + 1 : 0;
      lastH = h;
      if (stableFrames >= 2 || performance.now() - start > 800) {
        restoringRef.current = false;
        // PRESERVE THE INTENT, DO NOT RE-MEASURE IT. This used to recompute
        // `atBottom` from geometry at the moment the loop gave up, and on a long
        // thread still growing at the 800ms cap that computes false. Every
        // re-pin below is gated on this flag, so a thread the operator left at
        // the very bottom stopped following and stranded him mid-thread. That is
        // the "it loads to the middle, and not where I was either" report:
        // the target was the bottom, and the geometry answered for the frame
        // rather than for the intent. If we asked for the bottom, we are at the
        // bottom, and we keep following until he scrolls away from it.
        atBottomRef.current = toBottom
          || el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLOP;
        setShowJumpToLatest(!atBottomRef.current);
        return;
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, []);

  // Restore a thread's saved position when it opens. On a key change, seed the
  // target and pre-set atBottomRef from the saved spot so the follow effect below
  // does not yank a scrolled-up thread to the bottom before the restore runs. The
  // actual scroll waits for content: log fetch is async, and there is nothing to
  // scroll within until the transcript has arrived (hasContent). Restore fires
  // once per thread; streamed tokens after it are the follow effect's job.
  useLayoutEffect(() => {
    const key = restoreKey ?? null;
    if (restoreKeyRef.current !== key) {
      restoreKeyRef.current = key;
      restoredForRef.current = null;
      const saved = key ? loadScrollPos(key) : null;
      targetRef.current = saved;
      atBottomRef.current = saved ? saved.atBottom : true;
      setShowJumpToLatest(saved ? !saved.atBottom : false);
      // A hold captured for the thread being LEFT describes a DOM that is
      // about to be torn down (a fresh scroll container mounts for the new
      // thread, scrolled to the top). Applying it to the new thread's geometry
      // once its own `oldestSeq` happens to change would be a scroll jump with
      // no operator action behind it.
      olderHoldRef.current = null;
    }
    if (key && restoredForRef.current !== key && hasContent) {
      restoredForRef.current = key;
      settleRestore();
    }
  }, [restoreKey, hasContent, settleRestore]);

  // Follow only while the operator is actually following — but stand down while a
  // restore is settling, so it owns the position uncontested until it lands.
  useEffect(() => {
    if (restoringRef.current) return;
    if (atBottomRef.current) scrollToBottom();
    else if (hasContent) setShowJumpToLatest(true);
    // followDeps is the caller's spread list of things that mean "new content".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...followDeps, hasContent, scrollToBottom]);

  // The mobile keyboard opening/closing resizes the visual viewport.
  useEffect(() => {
    if (!inputFocused) return;
    const vv = window.visualViewport;
    const onResize = () => { if (atBottomRef.current) scrollToBottom(); };
    vv?.addEventListener("resize", onResize);
    return () => vv?.removeEventListener("resize", onResize);
  }, [inputFocused, scrollToBottom]);

  // Typing pins the log the same way new content does — but FOCUSING must
  // not. This only depends on `input`, not `inputFocused`: tapping into the
  // composer to reply to something further up used to re-run this on the
  // focus transition alone and throw the view back to the bottom, discarding
  // wherever you'd scrolled to read. `inputFocused` is read, not watched.
  const inputFocusedRef = useRef(inputFocused);
  useEffect(() => { inputFocusedRef.current = inputFocused; }, [inputFocused]);
  useEffect(() => {
    if (inputFocusedRef.current && atBottomRef.current) scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, scrollToBottom]);

  // The composer growing steals rows from the log.
  useEffect(() => {
    const el = composerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (atBottomRef.current) scrollToBottom(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [scrollToBottom]);

  // Auto-grow the composer: ~2.5 lines by default, grows with content, capped at
  // the CSS max-height (read from the element so the JS cap cannot drift from
  // the stylesheet); beyond that it scrolls.
  // The CSS cap is a constant, so it is read once per element instead of once
  // per keystroke. `getComputedStyle` forces a style recalc, and this one sat
  // directly in the typing path purely to re-learn the same number every time.
  // It is re-read if the textarea element itself is replaced.
  const maxHeightRef = useRef<{ el: HTMLTextAreaElement; max: number } | null>(null);
  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    if (maxHeightRef.current?.el !== el) {
      maxHeightRef.current = { el, max: parseFloat(getComputedStyle(el).maxHeight) || 96 };
    }
    // Expanded: hand the height back to the stylesheet. Clearing the inline
    // value rather than writing the expanded one keeps the size in ONE place
    // (--chat-input-expanded), so the desktop and mobile sizes stay a media
    // query rather than a second copy of the number in here.
    if (expanded) { el.style.height = ""; return; }
    const { max } = maxHeightRef.current;
    // The write-to-auto has to stay: it is what lets the box SHRINK, because
    // scrollHeight never reports less than the current fixed height.
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, [expanded]);
  useLayoutEffect(() => { autosize(); }, [input, autosize]);

  return {
    scrollRef, composerRef, inputRef, showJumpToLatest, onLogScroll, followLatest, holdForOlder,
  };
}
