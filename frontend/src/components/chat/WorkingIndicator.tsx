import { formatElapsed } from "./chatScope";

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
}: {
  label: string;
  // Omitted where there is nothing meaningful to count (a voice turn's
  // "Thinking" is sub-second and a timer there is noise).
  elapsed?: number;
}) {
  return (
    // chat-footrow: the working line, "Show more" and "Show less" are all the
    // last row of a bubble, so they share one spacing rule instead of each
    // carrying its own margin (which is why they didn't line up).
    <div className="chat-working chat-footrow" role="status" aria-live="polite">
      <span className="chat-working__label">{label}</span>
      <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
      {elapsed > 0 && <span className="chat-working__time">{formatElapsed(elapsed)}</span>}
    </div>
  );
}
