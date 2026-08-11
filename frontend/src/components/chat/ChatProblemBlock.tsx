import type { ReactNode } from "react";
import { Button, StateBlock } from "@workshop/ui";
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
// This is rule 9 of frontend/CLAUDE.md for the chat: the error state is a
// `StateBlock variant="error"`, it names the endpoint that failed, and it
// carries a retry when retrying is a real action. Failures that belong to one
// message do NOT come here — see chatProblem for the routing rule.

export default function ChatProblemBlock({
  problem, icon, className,
}: {
  problem: ChatProblem;
  /** Overrides the default alert triangle when the failure has a more specific
   *  shape (a dropped connection, a blocked network). */
  icon?: ReactNode;
  className?: string;
}) {
  const { title, detail } = problemBlock(problem);
  const endpoint = problem.endpoint || null;
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
        action={
          problem.retry
            ? <Button size="sm" variant="ghost" onClick={problem.retry}>Retry</Button>
            : undefined
        }
      />
    </div>
  );
}
