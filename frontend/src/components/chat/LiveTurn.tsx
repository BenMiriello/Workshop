import type { ChatSegment } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn } from "./Transcript";
import WorkingIndicator from "./WorkingIndicator";

// The turn currently streaming: ONE assistant bubble, with the working indicator
// at its tail.
//
// IT IS NOT CUT, and this is the correction to several days of getting it wrong.
// This component used to split the reply wherever a message arrived mid-turn and
// render that message inside the gap, so the operator's words appeared partway
// up the reply — and, when nothing had streamed yet, ABOVE the working indicator
// entirely. That last case is what made the error obvious: the work in flight
// was started WITHOUT the new message, so nothing about it can precede that work.
//
// The rule is the ordinary one every chat uses, and it is the one that was asked
// for: THE LOG IS APPEND-ONLY, IN SEND ORDER. A message typed while a reply is
// being written goes BELOW that reply and below its working indicator, because
// that is when it was sent and the reply had not seen it. See ChatLog, which
// renders exactly that order.
export default function LiveTurn({
  segments, streaming, workingLabel, elapsed, model, cardCtx,
}: {
  segments: ChatSegment[];
  streaming: boolean;
  workingLabel: string;
  elapsed: number;
  model: string;
  cardCtx: UiCardCtx;
}) {
  if (!segments.length && !streaming) return null;
  return (
    <div className="chat-msg assistant">
      <div className="chat-bubble">
        {segments.length > 0 && (
          <AssistantTurn
            segments={segments}
            isLatest
            model={model}
            cardCtx={cardCtx}
            streaming
          />
        )}
        {streaming && <WorkingIndicator label={workingLabel} elapsed={elapsed} />}
      </div>
    </div>
  );
}
