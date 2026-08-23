import { AlertTriangle, Hand } from "lucide-react";
import { Button } from "@workshop/ui";

// The queue only speaks when something has actually happened to it.
//
// The ordinary case (a message is waiting because a turn is running) shows NO
// line: the dimmed bubble already says it is pending, and each queued bubble
// carries its own unsend control in the corner. There is no standing explainer.
//
// Two states remain, both of which carry a Send now the bubbles cannot:
//
//   held   the operator pressed Stop, so nothing sends until they say so.
//   error  a send was attempted and failed. The text is safe; the reason is
//          theirs to read rather than swallowed into a log.
//
// This renders nothing when neither is true; the caller also guards on that.

export default function QueueNote({ held, error, onRelease }: {
  held: boolean;
  error: string;
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
  return null;
}
