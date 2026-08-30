import { useEffect, useState } from "react";
import { Eraser } from "lucide-react";
import { formatElapsed } from "./chatScope";
import StopButton from "./StopButton";

// THE CLEAR, WHILE IT IS HAPPENING.
//
// A clear is a server-side operation with no turn behind it, so without this
// line the operator presses it, the typed command vanishes (there is no request
// bubble for it, and that is correct, see useCompaction), and the transcript
// sits unchanged until the divider appears. From outside that is
// indistinguishable from a command that was dropped, which is exactly how it was
// reported: "it just disappeared, it didn't run".
//
// So it announces itself where its result will land: one line at the foot of the
// log, in the same divider language `ContextCut` uses, which the divider then
// REPLACES when the boundary lands. Same shape, same place, same words for the
// same action, so the line does not move and nothing is said twice.
//
// NOT A BANNER ABOVE THE LOG. That was the previous answer and it produced the
// sequence this file exists to avoid: a bar pinned above the transcript for the
// length of the operation, a bubble down in the chat afterwards, and any message
// sent meanwhile rendering above the banner, because a bar above the log has no
// position in time.
//
// A COMPACT NEVER DRAWS THIS. It used to: compaction was a ~30s Workshop-side
// summarizing fold that held the thread the same way a clear does. It is now the
// provider's own /compact, run as an ordinary turn, and a running turn is
// already the most visible thing on the screen.
//
// It carries its own clock. The transcript's 1Hz tick is gated on a run being
// active and a clear is not a run, so there is nothing to borrow; this interval
// exists only while the line is mounted.
export default function FoldingNote({ onStop, stopping = false }: {
  /** The cancel request is out. Without this the press had no visible effect at
   *  all and the line went on saying "Clearing" until the clear's own request
   *  returned, which is what made a working cancel read as a dead button. */
  stopping?: boolean;
  /** Abandon the clear. It holds the thread's queue while it runs, so being
   *  unable to call it off is a real corner to be stuck in. The server drops the
   *  cut rather than landing it; nothing is half-written, because the writes
   *  only ever happen at the end. */
  onStop?: () => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="chat-fold chat-fold--live" role="status" aria-live="polite">
      {onStop && <StopButton label="Stop clearing" onStop={onStop} busy={stopping} />}
      <Eraser size={14} strokeWidth={1.8} aria-hidden />
      <span>{stopping ? "Stopping the clear" : "Clearing context"}</span>
      <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
      {elapsed > 0 && <span className="chat-working__time">{formatElapsed(elapsed)}</span>}
    </div>
  );
}
