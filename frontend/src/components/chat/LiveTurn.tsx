import { type ReactNode } from "react";
import type { ChatSegment } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn } from "./Transcript";
import WorkingIndicator from "./WorkingIndicator";
import StatusNote from "./StatusNote";
import { cutPoints } from "./transcriptPlan";

// The turn currently streaming, cut at every point the operator interjected.
//
// With no interjections — the common case — this is exactly one assistant bubble
// carrying the streamed segments plus the working indicator. With them, the
// reply is SPLIT so each message sits between the output that preceded it and
// the output that followed, which is the order things actually happened in.
//
// Interjections live outside `messages` deliberately: that list renders above
// this overlay, so putting them there placed a later message above earlier
// output. Each carries the segment count at the moment it landed, which is where
// the cut goes.
//
// The working indicator belongs to the TAIL of the reply, so it renders below
// the newest interjection rather than stranded above it.
export default function LiveTurn({
  segments, interjections, streaming, workingLabel, elapsed, model, cardCtx,
}: {
  segments: ChatSegment[];
  interjections: { at: number; text: string; read?: boolean }[];
  streaming: boolean;
  workingLabel: string;
  elapsed: number;
  model: string;
  cardCtx: UiCardCtx;
}) {
  // Same rule the persisted list uses (transcriptPlan.cutPoints), so a message
  // does not move the instant this turn finishes and the log reloads.
  const cuts = cutPoints(interjections.map((x) => x.at), segments.length);
  const bounds = [0, ...cuts, segments.length];
  const blocks: ReactNode[] = [];

  for (let k = 0; k < bounds.length - 1; k++) {
    const slice = segments.slice(bounds[k], bounds[k + 1]);
    const isLastSlice = k === bounds.length - 2;
    const showWorking = streaming && isLastSlice;
    if (slice.length || showWorking) {
      blocks.push(
        <div className="chat-msg assistant" key={`live-a${k}`}>
          <div className="chat-bubble">
            {slice.length > 0 && (
              <AssistantTurn
                segments={slice}
                isLatest={isLastSlice}
                model={model}
                cardCtx={cardCtx}
                streaming
              />
            )}
            {showWorking && <WorkingIndicator label={workingLabel} elapsed={elapsed} />}
          </div>
        </div>,
      );
    }
    if (k < interjections.length) {
      // THREE STATES, AND THE LINE SAYS WHICH ONE IT IS.
      //
      // This used to claim "the running turn picked it up" from the moment the
      // write succeeded, which was only ever true of the bytes. Then it said
      // "handed over" and kept saying it for the whole turn — accurate, but a
      // pulsing "handed over" that never resolves is what the operator read as
      // the message sitting unread.
      //
      // `read` arrives when the turn echoes the message back
      // (--replay-user-messages), so "the turn has read this" is now a fact
      // rather than an inference. It still does not mean ACTED ON: measured, the
      // model addresses a mid-turn message at the end of its current plan (see
      // docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md §2),
      // which is why the line says read and not answered.
      const ij = interjections[k];
      blocks.push(
        <div className="chat-msg user" key={`live-ij${k}`}>
          <div className="chat-bubble">{ij.text}</div>
          {isLastSlice && streaming && (
            <StatusNote
              status={ij.read
                ? { text: "Read by the turn in progress", busy: false }
                : { text: "Handed to the turn in progress", busy: true }}
            />
          )}
        </div>,
      );
    }
  }
  return <>{blocks}</>;
}
