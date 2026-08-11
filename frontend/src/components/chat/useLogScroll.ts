import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

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
type ScrollPos = { top: number; atBottom: boolean };

function loadScrollPos(key: string): ScrollPos | null {
  try {
    const raw = sessionStorage.getItem(SCROLL_STORE_PREFIX + key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (typeof v?.top === "number" && typeof v?.atBottom === "boolean") return v;
  } catch { /* private mode / bad JSON — fall back to bottom */ }
  return null;
}

function saveScrollPos(key: string, pos: ScrollPos): void {
  try { sessionStorage.setItem(SCROLL_STORE_PREFIX + key, JSON.stringify(pos)); }
  catch { /* storage full or blocked — position memory is best-effort */ }
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
}: {
  input: string;
  inputFocused: boolean;
  followDeps: unknown[];
  hasContent: boolean;
  restoreKey?: string | null;
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

  // The ONLY writer of atBottomRef.
  const onLogScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distance < 80;
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

  const followLatest = useCallback(() => {
    atBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollToBottom(true);
  }, [scrollToBottom]);

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
    const step = () => {
      const el = scrollRef.current;
      if (!el) { restoringRef.current = false; return; }
      const t = targetRef.current;
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = !t || t.atBottom ? max : Math.min(t.top, max);
      const h = el.scrollHeight;
      stableFrames = h === lastH ? stableFrames + 1 : 0;
      lastH = h;
      if (stableFrames >= 2 || performance.now() - start > 800) {
        restoringRef.current = false;
        atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
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
  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const max = parseFloat(getComputedStyle(el).maxHeight) || 96;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, []);
  useLayoutEffect(() => { autosize(); }, [input, autosize]);

  return { scrollRef, composerRef, inputRef, showJumpToLatest, onLogScroll, followLatest };
}
