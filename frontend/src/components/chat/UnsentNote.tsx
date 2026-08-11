import { AlertTriangle } from "lucide-react";
import { Button } from "@workshop/ui";

// A MESSAGE THAT DID NOT SEND, SAID UNDER THAT MESSAGE.
//
// One component because there are two places the same failure can be sitting
// when the operator looks at it, and they must not read differently:
//
//   * the optimistic bubble, while this tab is still the one that tried;
//   * the durable queue entry, which is what survives a reload or a thread
//     switch (the reducer's copy does not).
//
// The reason is a sentence about this chat, not the exception that produced it.
// `String(e)` put "TypeError: Failed to fetch" here, in a slot above the
// composer that the next error overwrote and the next send cleared. See
// sendFailure.ts for the wording rule and frontend/CLAUDE.md for why a failure
// caused by a message belongs under it.

export default function UnsentNote({ reason, onRetry }: {
  reason?: string;
  onRetry: () => void;
}) {
  return (
    <div className="chat-unsent">
      <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
      <span>{reason || "Not sent: this message never reached the server. Press Retry."}</span>
      <Button size="sm" variant="ghost" onClick={onRetry}>Retry</Button>
    </div>
  );
}
