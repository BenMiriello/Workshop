import type { MsgStatus } from "../../lib/chat";

// A progress line attached to the message that caused it, rendered directly
// under that bubble.
//
// THIS IS DELIBERATELY NOT A BAR ABOVE THE LOG. Compaction used to report itself
// in the notice strip under the toolbar, and that placement was the bug: a note
// pinned above the whole transcript has no position in time, so anything sent
// while it was up rendered ABOVE the thing that was still happening. Anchored to
// its own message it is simply the next line in the log, and everything said
// afterwards necessarily follows it.
//
// It speaks the WORKING language (muted text, the same pulsing dots as the live
// turn indicator) rather than the accent card `.chat-note` uses. A note is a
// record of something that happened; this is something happening, and the two
// should not look alike.
export default function StatusNote({ status }: { status: MsgStatus }) {
  return (
    <div className="chat-status" role="status">
      {status.text}
      {status.busy && <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>}
    </div>
  );
}
