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
}: {
  input: string;
  inputFocused: boolean;
  followDeps: unknown[];
  hasContent: boolean;
}): LogScroll {
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Whether the log is scrolled to (near) the bottom, so focus/keyboard/viewport
  // changes only re-pin when the operator was already following.
  const atBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, []);

  // The ONLY writer of atBottomRef.
  const onLogScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = atBottom;
    setShowJumpToLatest((v) => (atBottom ? false : v));
  }, []);

  const followLatest = useCallback(() => {
    atBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollToBottom();
  }, [scrollToBottom]);

  // Follow only while the operator is actually following.
  useEffect(() => {
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

  useEffect(() => {
    if (inputFocused && atBottomRef.current) scrollToBottom();
  }, [input, inputFocused, scrollToBottom]);

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
