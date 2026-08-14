import { Loader2, Square } from "lucide-react";

// STOP, WHERE THE WORK IS. Not under the composer.
//
// It used to sit in the composer's action row beside Send, which put the control
// that ends a turn nowhere near the thing it ends: the working line is down in
// the transcript, and on a long log it is often not even on screen with the
// composer. Operator, 2026-08-14: put it "just to the left of the working
// indicator", and "anywhere that the system is working in the chat".
//
// So this is one control with one shape, rendered by every line that represents
// work in flight (WorkingIndicator, FoldingNote). A circle around the square,
// because at 12px a bare glyph on a text line reads as an icon labelling the
// row rather than a thing to press.
//
// It never renders when there is nothing to stop: the caller passes no handler,
// and a disabled stop would be a promise that the work could be ended if only
// you were allowed to ask.
export default function StopButton({ label, onStop, busy = false }: {
  /** What gets stopped, for the accessible name: "Stop the turn", "Stop compacting". */
  label: string;
  onStop: () => void;
  /** The request is in flight. Ending a turn takes as long as it takes the
   *  server to reach the run and commit what it produced, and with no state on
   *  the button a press looked like it had done nothing. */
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      className={`chat-stopdot${busy ? " is-busy" : ""}`}
      // Inert while the request is out. A second press cannot make it stop
      // harder, and the first one is already doing the only thing there is.
      disabled={busy}
      aria-busy={busy || undefined}
      // Cancels pointerdown so pressing it never blurs the composer, which on a
      // phone would close the keyboard and move this button out from under the
      // finger mid-tap. Same rule as every control in the composer's action row.
      onPointerDown={(e) => e.preventDefault()}
      onClick={onStop}
      aria-label={busy ? "Stopping" : label}
      title={busy ? "Stopping" : label}
    >
      {/* The square becomes the spinner IN PLACE, in the same ring, so the press
          reads as this control changing state rather than as one control being
          swapped for another. `ws-spin` is the kit's own rotation (it already
          slows under prefers-reduced-motion), not a second spinner invented
          here. 12px because a ring reads smaller than a filled square at the
          same nominal size. */}
      {busy
        ? <Loader2 size={12} strokeWidth={2.5} aria-hidden className="ws-spin" />
        : <Square size={11} strokeWidth={3} aria-hidden />}
    </button>
  );
}
