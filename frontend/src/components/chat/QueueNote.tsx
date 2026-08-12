import { AlertTriangle, Clock, Hand } from "lucide-react";
import { Button } from "@workshop/ui";

// WHAT THE WAITING MESSAGES ARE WAITING FOR, said once, under all of them.
//
// The chat runs one turn at a time and some turns read nothing at all (a slash
// command, a voice turn), so a message sent during one waits. What matters is
// WHERE it waits, and this line is the operator-facing half of that answer: it
// waits on the server, on this thread. It will be sent whether or not this tab
// is open, whether or not this device comes back, and it is the same queue in
// every window looking at this chat.
//
// It said "sends when the current turn finishes" before, from a browser that
// could only keep that promise if it was still around to keep it. It was not,
// on 2026-08-12, and two messages ceased to exist.
//
// Three states, because there are exactly three reasons a message is sitting
// here, and the operator's next move differs for each:
//
//   waiting  a turn is running. Nothing to do; it goes when that ends.
//   held     they pressed Stop. Nothing goes until they say so.
//   error    a send was attempted and failed. The text is safe, and the reason
//            is theirs to read rather than something swallowed into a log.

export default function QueueNote({ count, held, error, onRelease, onCancelLast }: {
  count: number;
  held: boolean;
  error: string;
  onRelease: () => void;
  onCancelLast: () => void;
}) {
  const n = count === 1 ? "This message is" : `These ${count} messages are`;
  if (error) {
    return (
      <div className="chat-unsent">
        <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
        <span>{n} saved and not sent yet. {error}</span>
        <Button size="sm" variant="ghost" onClick={onRelease}>Send now</Button>
      </div>
    );
  }
  if (held) {
    return (
      <div className="chat-unsent">
        <Hand size={13} strokeWidth={1.8} aria-hidden />
        <span>{n} waiting. You stopped the turn, so nothing sends until you say so.</span>
        <Button size="sm" variant="ghost" onClick={onRelease}>Send now</Button>
      </div>
    );
  }
  return (
    <div className="chat-queued-note">
      <Clock size={13} strokeWidth={1.8} aria-hidden />
      <span>{n} saved, and go out when this turn finishes.</span>
      <Button size="sm" variant="ghost" onClick={onCancelLast}>Unsend last</Button>
    </div>
  );
}
