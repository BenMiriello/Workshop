import { useEffect, useState } from "react";
import { Archive, Eraser } from "lucide-react";
import { formatElapsed } from "./chatScope";

// THE FOLD, WHILE IT IS HAPPENING.
//
// /compact is a ~30s model call and it used to say nothing at all while it ran.
// The operator ran it, his typed command vanished (there is no request bubble
// for it, and that is correct — see useCompaction), and the transcript sat
// unchanged until the divider appeared half a minute later. From outside that
// is indistinguishable from a command that was dropped, which is exactly how it
// was reported: "it just disappeared, it didn't run".
//
// So the fold announces itself where its result will land: one line at the foot
// of the log, in the same divider language `ContextCut` uses, which the divider
// then REPLACES when the boundary lands. Same shape, same place, same words for
// the same action — the line does not move and nothing is said twice.
//
// NOT A BANNER ABOVE THE LOG. That was the previous answer and it produced the
// sequence this file exists to avoid: a bar pinned above the transcript for the
// length of the fold, a bubble down in the chat afterwards, and any message sent
// meanwhile rendering above the banner, because a bar above the log has no
// position in time.
//
// It carries its own clock. The transcript's 1Hz tick is gated on a run being
// active and a fold is not a run, so there is nothing to borrow; this interval
// exists only while the line is mounted.
export default function FoldingNote({ mode }: { mode: "compact" | "clear" }) {
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
      {mode === "compact"
        ? <Archive size={14} strokeWidth={1.8} aria-hidden />
        : <Eraser size={14} strokeWidth={1.8} aria-hidden />}
      <span>
        {mode === "compact"
          ? "Compacting. Folding earlier messages into a summary"
          : "Clearing context"}
      </span>
      <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
      {elapsed > 0 && <span className="chat-working__time">{formatElapsed(elapsed)}</span>}
    </div>
  );
}
