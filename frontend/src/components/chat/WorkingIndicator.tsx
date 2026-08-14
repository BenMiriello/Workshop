import { formatElapsed } from "./chatScope";
import StopButton from "./StopButton";

// "Something is happening" — the one animated thing in the transcript.
//
// The LABEL is load-bearing. It used to always read "Working", which is the one
// word that tells you nothing about the several seconds you are waiting through:
// a reconnect after a dropped stream and a context compaction both looked
// identical to ordinary work. Each state that a turn can genuinely be in gets
// its own word.
export default function WorkingIndicator({
  label,
  elapsed = 0,
  onStop,
  stopping = false,
}: {
  label: string;
  // Omitted where there is nothing meaningful to count (a voice turn's
  // "Thinking" is sub-second and a timer there is noise).
  elapsed?: number;
  /** End the turn. Absent where this line is not something the operator can
   *  stop (the voice shell drives its own turn), and then no control renders. */
  onStop?: () => void;
  /** The stop request is out. The label already reads "Stopping" (turnStatus
   *  owns that); this is what puts the same state on the control. */
  stopping?: boolean;
}) {
  return (
    // chat-footrow: the working line, "Show more" and "Show less" are all the
    // last row of a bubble, so they share one spacing rule instead of each
    // carrying its own margin (which is why they didn't line up).
    <div className="chat-working chat-footrow" role="status" aria-live="polite">
      {/* Left of the label, so the control sits at the head of the line that
          says work is happening rather than after the clock. */}
      {onStop && <StopButton label="Stop the turn" onStop={onStop} busy={stopping} />}
      <span className="chat-working__label">{label}</span>
      <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
      {elapsed > 0 && <span className="chat-working__time">{formatElapsed(elapsed)}</span>}
    </div>
  );
}
