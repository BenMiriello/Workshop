import { AlertTriangle, Gauge, Hand } from "lucide-react";
import { Button } from "@workshop/ui";
import type { QueueWaiting } from "../../lib/chat";
import { waitingText } from "./sendRules";

// The queue only speaks when something has actually happened to it.
//
// The ordinary case (a message is waiting because a turn is running) shows NO
// line: the dimmed bubble already says it is pending, and each queued bubble
// carries its own unsend control in the corner. There is no standing explainer.
//
// Three states remain, all of which carry a Send now the bubbles cannot:
//
//   held   the operator pressed Stop, so nothing sends until they say so.
//   error  a send was attempted and failed. The text is safe; the reason is
//          theirs to read rather than swallowed into a log.
//   waiting  the platform is holding the turn back (the usage limit is hit),
//          and sends it by itself once the reason passes.
//
// This renders nothing when neither is true; the caller also guards on that.

export default function QueueNote({ held, error, waiting, onRelease }: {
  held: boolean;
  error: string;
  waiting: QueueWaiting | null;
  onRelease: () => void;
}) {
  if (error) {
    return (
      <div className="chat-unsent">
        <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
        <span>Saved and not sent yet. {error}</span>
        <Button size="sm" variant="ghost" onClick={onRelease}>Send now</Button>
      </div>
    );
  }
  if (held) {
    return (
      <div className="chat-unsent">
        <Hand size={13} strokeWidth={1.8} aria-hidden />
        <span>Waiting. You stopped the turn, so nothing sends until you say so.</span>
        <Button size="sm" variant="ghost" onClick={onRelease}>Send now</Button>
      </div>
    );
  }
  if (waiting) {
    return (
      <div className="chat-unsent">
        <Gauge size={13} strokeWidth={1.8} aria-hidden />
        <span>{waitingText(waiting)}</span>
        <Button size="sm" variant="ghost" onClick={onRelease}>Send now</Button>
      </div>
    );
  }
  return null;
}
