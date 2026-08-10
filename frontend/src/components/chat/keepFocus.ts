import type { PointerEvent as ReactPointerEvent } from "react";

// TAPPING A CONTROL MUST NOT COST A TAP.
//
// With the mobile keyboard up, tapping context / effort / mode / model did
// nothing but dismiss the keyboard: the touch blurred the textarea, the
// keyboard closed, the layout reflowed under the finger, and the click landed
// on whatever had moved into that spot — or on nothing. The operator had to tap
// once to close the keyboard and again to hit the button.
//
// The controls each carried `onMouseDown={e => e.preventDefault()}`, which is
// the desktop half of the fix and useless on touch: the compatibility mousedown
// arrives AFTER touchend, long after focus has already moved. Cancelling
// POINTERdown is what works on both — it suppresses the focus change (and the
// compatibility mouse events with it) while still dispatching click.
//
// Applied to the CONTAINER (the toolbar row, the composer's action row), not to
// each button, so a control added later inherits it. Text inputs inside the
// container are exempt: tapping one is a request to move focus.
export function keepFocusOnTap(e: ReactPointerEvent<HTMLElement>): void {
  const el = e.target as HTMLElement | null;
  if (el?.closest("input, textarea, select, [contenteditable='true']")) return;
  if (e.cancelable) e.preventDefault();
}
