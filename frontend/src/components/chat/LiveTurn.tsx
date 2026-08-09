import type { ChatSegment } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn } from "./Transcript";
import WorkingIndicator from "./WorkingIndicator";

// ONE CONTIGUOUS STRETCH of the turn currently running: an assistant bubble,
// with the working indicator on the stretch that is still being written.
//
// A turn is not one thing that happened at one moment; it is work over an
// interval. A message sent during that interval divides it, so a running turn
// can be several of these, with the operator's messages between them — the work
// above a message was emitted before it, the work below was produced by a turn
// that had already been handed it. Where the cuts fall is decided by
// transcriptBlocks, not here; this renders one piece.
//
// `working` marks the last piece. It is driven by whether a RUN is in flight
// (server state), not by whether this tab has a socket open: a turn whose stream
// dropped past its reconnect budget, or one the operator stopped watching, is
// still running and still says so.
export default function LiveTurn({
  segments, working, workingLabel, elapsed, model, cardCtx,
}: {
  segments: ChatSegment[];
  working: boolean;
  workingLabel: string;
  elapsed: number;
  model: string;
  cardCtx: UiCardCtx;
}) {
  if (!segments.length && !working) return null;
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
        {working && <WorkingIndicator label={workingLabel} elapsed={elapsed} />}
      </div>
    </div>
  );
}
