import { useEffect, useRef, useState, type ReactNode } from "react";

// ONE WAY TO OPEN AND CLOSE THINGS, so every disclosure in the chat moves the
// same way. Before this they all snapped: a tool call's output, a group's
// steps, a delegation's body and a settled turn's work all appeared and
// vanished between frames, which reads as the page jumping rather than as
// something opening. Operator asked for the whole set, 2026-08-12.
//
// HOW IT ANIMATES TO AN UNKNOWN HEIGHT. A CSS transition needs two concrete
// values and "as tall as its content" is not one, so the height is not
// animated at all: the wrapper is a one-row grid whose row goes 0fr -> 1fr,
// with the inner element at `overflow: hidden; min-height: 0`. The grid
// resolves the fraction against the real content height on every frame, which
// is what makes it work for content nobody measured. It needs no JS
// measurement, cannot be wrong about the height, and works in every browser
// this app runs in. `interpolate-size` would be tidier but is Chromium-only as
// of August 2026, so it is used nowhere load-bearing.
//
// WHY THE CHILDREN STILL UNMOUNT. Keeping every group's steps and every
// settled turn's work mounted just so they could animate would undo the
// density the transcript is built around. So the WRAPPER is always in the DOM
// (which keeps `aria-controls` pointing at something real) and the CHILDREN
// mount on open and unmount one duration after close, once the collapse has
// actually finished.

/** Must match `--dur-base` in tokens.css. Only used to know when the collapse
 *  has finished and the children can go; the animation itself is pure CSS. */
const REVEAL_MS = 200;

// ONLY WHAT THE OPERATOR DID ANIMATES. The machine's own opening and closing
// is instant, and that is not a compromise, it is the fix for a real bug.
//
// Re-attaching to a turn in flight REPLAYS IT FROM OFFSET 0 (useTurnStream: a
// fresh EventSource carries no Last-Event-ID, so the server sends the whole
// turn again). Every group in that turn therefore opens and closes for real, in
// a few hundred milliseconds, as the replay walks past. With machine
// transitions animated, opening the chat on a long-running turn fired dozens of
// animations at once. The operator reported exactly that on 2026-08-12.
//
// A window guard or a settling delay would only paper over it, because a replay
// takes as long as it takes. Keying on WHO caused the change removes the
// problem by construction: a replay produces no operator actions, so a replay
// animates nothing. The caller already tracks this, in `disclosure.ts`.

/** `mounted` is whether to render the children, `shown` is whether the row is
 *  open. They differ only during a collapse, which is the whole point.
 *
 *  `animate` false means jump straight there: no frames, no unmount delay. */
export function useReveal(open: boolean, animate = true): { mounted: boolean; shown: boolean } {
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(open);
  useEffect(() => {
    if (!animate) {
      setMounted(open);
      setShown(open);
      return;
    }
    if (open) {
      setMounted(true);
      // TWO frames, not one. The children mount in the first, and the class
      // that opens the row must land in a LATER frame or the browser coalesces
      // the two and there is no start value to transition from.
      let second = 0;
      const first = requestAnimationFrame(() => { second = requestAnimationFrame(() => setShown(true)); });
      return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); };
    }
    setShown(false);
    const t = setTimeout(() => setMounted(false), REVEAL_MS);
    return () => clearTimeout(t);
  }, [open, animate]);
  // Opening on first render is not an animation: a settled turn the reader
  // opened, or a group that mounts mid-flight, is already open when it appears
  // and must not slide in. Both states seed from `open`, so nothing moves.
  return { mounted, shown };
}

/** A collapsible region. The caller keeps owning the open state; this owns only
 *  how it gets there. */
export function Reveal({ open, id, className, animate, children }: {
  open: boolean;
  /** For the trigger's `aria-controls`. The wrapper stays mounted so this
   *  always resolves, even collapsed. */
  id?: string;
  className?: string;
  /** Default true, for a disclosure only the operator ever opens. Pass the
   *  provenance flag for one the machine also opens. See the note above. */
  animate?: boolean;
  children: ReactNode;
}) {
  const { mounted, shown } = useReveal(open, animate);
  return (
    // `is-instant` kills the CSS transition too. Setting the open state without
    // frames is not enough on its own: grid-template-rows would still animate
    // from whatever it was, which is the burst all over again.
    <div
      id={id}
      className={`chat-reveal${shown ? " is-shown" : ""}${animate === false ? " is-instant" : ""}`}
      aria-hidden={!shown || undefined}
    >
      <div className={`chat-reveal__inner${className ? ` ${className}` : ""}`}>
        {mounted ? children : null}
      </div>
    </div>
  );
}

/** True for one animation's length after `value` changes, and never on the
 *  first render.
 *
 *  For the two places that animate a property they also set for other reasons.
 *  The composer's textarea has its height written by the autosize handler on
 *  every keystroke, and a clamped message's height changes as tokens stream in;
 *  a permanent transition on either would animate typing and streaming. Gating
 *  the transition on a class that only exists just after a click means the
 *  animation happens when the operator asked for it and at no other time. */
export function useToggleAnim(value: unknown): boolean {
  const [anim, setAnim] = useState(false);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    setAnim(true);
    const t = setTimeout(() => setAnim(false), REVEAL_MS);
    return () => clearTimeout(t);
  }, [value]);
  return anim;
}
