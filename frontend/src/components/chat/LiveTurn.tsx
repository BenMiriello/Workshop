import { type ReactNode } from "react";
import type { ChatSegment } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn } from "./Transcript";
import WorkingIndicator from "./WorkingIndicator";
import StatusNote from "./StatusNote";

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
  interjections: { at: number; text: string }[];
  streaming: boolean;
  workingLabel: string;
  elapsed: number;
  model: string;
  cardCtx: UiCardCtx;
}) {
  const cuts = interjections.map((x) => Math.min(x.at, segments.length));
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
      // The status line said "the running turn picked it up". It had not. What
      // is known is that the message reached the running process; measured
      // against the CLI, the agent then addresses it when it reaches the end of
      // its current plan, skipping the step boundaries in between (see
      // docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md §2).
      // Claiming it was read is the difference between a chat that is waiting
      // and a chat that looks broken, so say the true thing.
      blocks.push(
        <div className="chat-msg user" key={`live-ij${k}`}>
          <div className="chat-bubble">{interjections[k].text}</div>
          {isLastSlice && streaming && (
            <StatusNote status={{ text: "Handed to the turn in progress", busy: true }} />
          )}
        </div>,
      );
    }
  }
  return <>{blocks}</>;
}
