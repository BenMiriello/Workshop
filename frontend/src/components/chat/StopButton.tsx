import { Square } from "lucide-react";

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
export default function StopButton({ label, onStop }: {
  /** What gets stopped, for the accessible name: "Stop the turn", "Stop compacting". */
  label: string;
  onStop: () => void;
}) {
  return (
    <button
      type="button"
      className="chat-stopdot"
      // Cancels pointerdown so pressing it never blurs the composer, which on a
      // phone would close the keyboard and move this button out from under the
      // finger mid-tap. Same rule as every control in the composer's action row.
      onPointerDown={(e) => e.preventDefault()}
      onClick={onStop}
      aria-label={label}
      title={label}
    >
      <Square size={9} strokeWidth={3} aria-hidden />
    </button>
  );
}
