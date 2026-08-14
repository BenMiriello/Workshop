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
// Applied to the CONTAINER, not to each button, so a control added later
// inherits it.
//
// THE TEST IS "IS THIS A CONTROL", NOT "IS THIS NOT A FIELD", and the inversion
// is what lets it cover the whole chat panel. It used to cancel pointerdown on
// anything that was not an input, which is safe on a toolbar and destructive
// over a transcript: it would kill long-press selection and the iOS copy callout
// on every message. So it was pinned to three containers (the toolbar, the
// composer's action row, the input wrap) and NOTHING in the log had it — not the
// copy button, not the branch control, not the approval buttons, not the jump
// pill, which still carried the useless `onMouseDown` version. Asking about the
// control instead means one handler on `.chat-panel` covers all of them and
// leaves ordinary text alone.
//
// This is only ever half the fix, and the smaller half. Cancelling pointerdown
// suppresses the focus change on Blink but not reliably on WebKit, so on iOS the
// keyboard may still close. What makes the tap REGISTER regardless is that
// nothing moves during the gesture any more: see useVisualViewport's
// settleAfterGesture and the composer's `--app-kb` padding.
export function keepFocusOnTap(e: ReactPointerEvent<HTMLElement>): void {
  const el = e.target as HTMLElement | null;
  if (!el?.closest("button, label, [role='menuitem'], [role='button'], [role='tab']")) return;
  if (e.cancelable) e.preventDefault();
}
