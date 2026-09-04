import type { ReactNode } from "react";
import { Button, Notice, StateBlock } from "@workshop/ui";
import { problemBlock, type ChatProblem } from "./chatProblem";

// A FAILURE OF THE WHOLE CHAT, said one way.
//
// The chat had three ways of reporting a problem: a bare red line above the
// composer, a `StateBlock` for blocked egress, and a note under the message
// that caused it. The first was the odd one out — anonymous red text with no
// endpoint, no retry, and no clue whether the thing that failed was this
// message, this thread, or the backend. It is gone, and everything that used it
// renders here.
//
// TWO SHAPES, and the difference is whether there is a transcript on screen:
//
//   "line"   the default, and what sits above the composer. The chat is full of
//            valid history and one thing went wrong beside it, so it costs one
//            line. Consolidating on the region made a dropped poll render as a
//            tall centred dashed panel over the composer; the operator's words,
//            2026-08-12, were "huge and blocky in the chat", and he was right.
//   "region" ONLY where this stands in for the log itself: the log's error
//            state, an empty drawer, an inbox that would not load. Nothing else
//            is on screen, so the block is filling space rather than taking it.
//
// Failures that belong to one message do NOT come here — see chatProblem for
// the routing rule.

export default function ChatProblemBlock({
  problem, icon, shape = "line", className, onDismiss,
}: {
  problem: ChatProblem;
  /** Overrides the default alert triangle when the failure has a more specific
   *  shape (a dropped connection, a blocked network). */
  icon?: ReactNode;
  /** "region" only when nothing else is on screen. See above. */
  shape?: "line" | "region";
  className?: string;
  /** Take this one off the screen. Offered where the failure has no Retry,
   *  which is exactly the set that cannot resolve itself: a blocked write, a
   *  tree taken by another session, a refused command. Those have no condition
   *  to recover, so without this they are stuck by construction, and getting
   *  stuck is what the operator reported on 2026-09-04. A failure that DOES
   *  carry a Retry keeps the one control: retrying is the useful move, and the
   *  reader that raised it clears it when the condition heals (see
   *  chat/problemState). */
  onDismiss?: () => void;
}) {
  const { title, detail } = problemBlock(problem);
  const endpoint = problem.endpoint || null;
  const retry = problem.retry
    ? <Button size="sm" variant="ghost" onClick={problem.retry}>Retry</Button>
    : onDismiss
      ? <Button size="sm" variant="ghost" onClick={onDismiss}>Dismiss</Button>
      : undefined;

  if (shape === "line") {
    return (
      <Notice
        tone={problem.tone ?? "error"}
        icon={icon}
        detail={detail}
        where={endpoint}
        action={retry}
        className={className ? `chat-problem ${className}` : "chat-problem"}
      >
        {title}
      </Notice>
    );
  }

  return (
    <div className={className ? `chat-problem ${className}` : "chat-problem"}>
      <StateBlock
        variant="error"
        icon={icon}
        title={title}
        hint={
          detail || endpoint ? (
            <>
              {detail && <span>{detail}</span>}
              {endpoint && (
                <div className="chat-problem__endpoint">
                  <code>{endpoint}</code>
                </div>
              )}
            </>
          ) : undefined
        }
        action={retry}
      />
    </div>
  );
}
