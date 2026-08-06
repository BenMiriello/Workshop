import { Fragment } from "react";
import { AlertTriangle, Bot, Clock, FoldVertical } from "lucide-react";
import { Button } from "../../ui";
import type { ChatMsg, ChatSegment } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn, AnswerBody, ElidedContent } from "./Transcript";
import StatusNote from "./StatusNote";
import { modelDisplay } from "./models";
import { planTranscript, latestAssistantRow } from "./transcriptPlan";

// The persisted transcript.
//
// IT RENDERS THE SAME SHAPE THE LIVE TURN DID. A message sent while a reply was
// being written is stored AFTER that reply (causally true) with `at_segment`
// recording where it landed. This list used to ignore that and render the stored
// order flat, so the moment a turn ended the operator's message jumped from the
// middle of the reply to below all of it — and the reply's answer to it ended up
// above it. That reads as "you never saw my message", and on 2026-08-06 it read
// that way to the operator, who re-sent a message that had already been acted
// on. The rule for where it goes lives in transcriptPlan and is now used by both
// views.
//
// Four annotations here exist because their absence was itself a bug:
//
//  - THE FOLD MARKER. Compaction never drops raw messages, so without a divider
//    a compacted thread looks identical to an uncompacted one and the operator
//    cannot tell whether /compact did anything.
//  - THE AGENT BYLINE. A reply from a mentioned agent is not the assistant's,
//    and without a byline there is no way to tell who answered — which is the
//    whole point of addressing one. Only agent replies carry it; the assistant
//    is the unmarked default because it is the overwhelming majority.
//  - THE UNSENT MARK. An optimistic bubble that quietly never reached the server
//    is exactly what "my messages keep disappearing" felt like from outside. It
//    is marked, and it offers the retry.
//  - THE STATUS LINE. Progress on work a message set off belongs under that
//    message, not in a bar above the log — see StatusNote.

function renderAssistantBody(
  m: ChatMsg, isLatest: boolean, fallbackModel: string, cardCtx: UiCardCtx,
  segmentsOverride?: ChatSegment[],
) {
  if (m.meta?.error) return m.content;
  const model = m.meta?.model ?? fallbackModel;
  const segments = segmentsOverride ?? m.meta?.segments;
  if (segments && segments.length) {
    return <AssistantTurn segments={segments} isLatest={isLatest} model={model} cardCtx={cardCtx} />;
  }
  return <AnswerBody source={m.content} cfg={modelDisplay(model)} expanded={isLatest} cardCtx={cardCtx} />;
}

export default function MessageList({
  messages, compactedThrough, selectedModel, cardCtx, expandLatest, onRetryUnsent,
}: {
  messages: ChatMsg[];
  compactedThrough: number;
  selectedModel: string;
  cardCtx: UiCardCtx;
  // Recency nuance: the most recent assistant answer is expanded and older ones
  // auto-shorten — but only when nothing is streaming, because a live turn is
  // itself the most recent thing.
  expandLatest: boolean;
  onRetryUnsent: (index: number, text: string) => void;
}) {
  const threadCfg = modelDisplay(selectedModel);
  const rows = planTranscript(messages);
  const latestRow = latestAssistantRow(rows);
  // The fold counts MESSAGES, so it is drawn before the first row belonging to
  // that message — a split reply must not draw it once per slice.
  let foldDrawn = false;

  return (
    <>
      {rows.map((row, r) => {
        const m = row.msg;
        const slice = row.kind === "slice" ? row : null;
        // A split reply carries its whole-reply annotations on its first slice
        // and its trailing ones (notes, status) on its last, so neither is
        // repeated at every cut.
        const isHead = !slice || slice.head;
        const isTail = !slice || slice.tail;
        const drawFold = compactedThrough > 0 && row.index === compactedThrough && !foldDrawn;
        if (drawFold) foldDrawn = true;

        return (
          <Fragment key={`${row.index}:${r}`}>
            {drawFold && (
              <div className="chat-fold" role="separator">
                <FoldVertical size={14} strokeWidth={1.8} aria-hidden />
                <span>
                  {compactedThrough} earlier {compactedThrough === 1 ? "message" : "messages"} folded
                  into the running summary
                </span>
              </div>
            )}
            <div className={`chat-msg ${m.role}`}>
              <div className={`chat-bubble ${m.meta?.error ? "error" : ""}${m.meta?.timeout ? " chat-bubble--timeout" : ""}`}>
                {m.role === "assistant" ? (
                  <>
                    {isHead && m.meta?.agent && (
                      <div className="chat-byline">
                        <Bot size={13} strokeWidth={1.8} aria-hidden />
                        <span>{m.meta.agent}</span>
                      </div>
                    )}
                    {isHead && m.meta?.timeout && (
                      <div className="chat-timeout-tag">
                        <Clock size={13} strokeWidth={1.8} aria-hidden /> Turn stopped at the time limit
                      </div>
                    )}
                    {renderAssistantBody(
                      m, expandLatest && r === latestRow, selectedModel, cardCtx, slice?.segments,
                    )}
                  </>
                ) : (
                  <>
                    <ElidedContent source={m.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
                    {m.meta?.unsent && (
                      <div className="chat-unsent">
                        <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
                        <span>Not sent, this message never reached the server.</span>
                        <Button size="sm" variant="ghost" onClick={() => onRetryUnsent(row.index, m.content)}>Retry</Button>
                      </div>
                    )}
                  </>
                )}
              </div>
              {isTail && m.meta?.notes?.map((n, j) => (
                <div key={j} className="chat-note">{n}</div>
              ))}
              {isTail && m.meta?.status && <StatusNote status={m.meta.status} />}
            </div>
          </Fragment>
        );
      })}
    </>
  );
}
