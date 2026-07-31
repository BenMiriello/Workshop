import { Fragment } from "react";
import { AlertTriangle, Bot, Clock, FoldVertical } from "lucide-react";
import { Button } from "../../ui";
import type { ChatMsg } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn, AnswerBody, ElidedContent } from "./Transcript";
import { modelDisplay } from "./models";

// The persisted transcript.
//
// Three annotations here exist because their absence was itself a bug:
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

function renderAssistantBody(m: ChatMsg, isLatest: boolean, fallbackModel: string, cardCtx: UiCardCtx) {
  if (m.meta?.error) return m.content;
  const model = m.meta?.model ?? fallbackModel;
  if (m.meta?.segments && m.meta.segments.length) {
    return <AssistantTurn segments={m.meta.segments} isLatest={isLatest} model={model} cardCtx={cardCtx} />;
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
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { lastAssistantIdx = i; break; }
  }

  return (
    <>
      {messages.map((m, i) => (
        <Fragment key={i}>
          {compactedThrough > 0 && i === compactedThrough && (
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
                  {m.meta?.agent && (
                    <div className="chat-byline">
                      <Bot size={13} strokeWidth={1.8} aria-hidden />
                      <span>{m.meta.agent}</span>
                    </div>
                  )}
                  {m.meta?.timeout && (
                    <div className="chat-timeout-tag">
                      <Clock size={13} strokeWidth={1.8} aria-hidden /> Turn stopped at the time limit
                    </div>
                  )}
                  {renderAssistantBody(m, expandLatest && i === lastAssistantIdx, selectedModel, cardCtx)}
                </>
              ) : (
                <>
                  <ElidedContent source={m.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
                  {m.meta?.unsent && (
                    <div className="chat-unsent">
                      <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
                      <span>Not sent, this message never reached the server.</span>
                      <Button size="sm" variant="ghost" onClick={() => onRetryUnsent(i, m.content)}>Retry</Button>
                    </div>
                  )}
                </>
              )}
            </div>
            {m.meta?.notes?.map((n, j) => (
              <div key={j} className="chat-note">{n}</div>
            ))}
          </div>
        </Fragment>
      ))}
    </>
  );
}
