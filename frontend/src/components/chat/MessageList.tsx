import { Fragment } from "react";
import { AlertTriangle, Bot, Clock, Eraser, FoldVertical, Unplug } from "lucide-react";
import { Button } from "../../ui";
import type { ChatMsg } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn, AnswerBody, ElidedContent } from "./Transcript";
import CopyMessage from "./CopyMessage";
import StatusNote from "./StatusNote";
import { modelDisplay } from "./models";
import { contextCutMark } from "./contextCut";

// The persisted transcript.
//
// These annotations exist because their absence was itself a bug:
//
//  - THE FOLD MARKER. Compaction never drops raw messages, so without a divider
//    a compacted thread looks identical to an uncompacted one and the operator
//    cannot tell whether /compact did anything. /clear draws the same divider
//    with different wording and icon (Eraser, not FoldVertical) — folding
//    preserves a summary, clearing does not, and the wording must never claim
//    otherwise.
//  - THE AGENT BYLINE. A reply from a mentioned agent is not the assistant's,
//    and without a byline there is no way to tell who answered — which is the
//    whole point of addressing one. Only agent replies carry it; the assistant
//    is the unmarked default because it is the overwhelming majority.
//  - THE UNSENT MARK. An optimistic bubble that quietly never reached the server
//    is exactly what "my messages keep disappearing" felt like from outside. It
//    is marked, and it offers the retry.
//  - THE STATUS LINE. Progress on work a message set off belongs under that
//    message, not in a bar above the log — see StatusNote.
//  - THE INTERRUPTED TAG. A message left mid-stream by a backend restart is
//    never going to finish — the self-heal in GET /threads/{id} marks it
//    `meta.stopped`. Without a tag it rendered as a normal reply that just
//    happens to be blank (or truncated wherever the last flush landed), which
//    reads as either "answered with nothing" or "broken", not "interrupted".

// One divider, two meanings. `cleared` is the ONLY thing that decides which:
// a fold's summary is real content the operator can still draw on; a clear's
// is not, so the wording never uses the word "summary".
function ContextCut({ count, cleared }: { count: number; cleared: boolean }) {
  return (
    <div className="chat-fold" role="separator">
      {cleared
        ? <Eraser size={14} strokeWidth={1.8} aria-hidden />
        : <FoldVertical size={14} strokeWidth={1.8} aria-hidden />}
      <span>
        {cleared
          ? `Context cleared. ${count} earlier ${count === 1 ? "message is" : "messages are"} no longer sent to the model`
          : `${count} earlier ${count === 1 ? "message" : "messages"} folded into the running summary`}
      </span>
    </div>
  );
}

// Why a turn produced no text, said in one plain sentence.
//
// A bubble with nothing in it is the worst of the three possible renderings: it
// says something happened without saying what, and reads as a rendering bug
// rather than as a turn that ended early. The `timeout`/`stopped` cases already
// carry a tag above the body, so here they only need to answer the question that
// tag leaves open — was anything written before it ended? The last case is the
// genuinely unexplained one, and it says so rather than showing an empty box.
function noReplyText(m: ChatMsg): string {
  if (m.meta?.timeout) return "No reply was written before the time limit.";
  if (m.meta?.stopped) return "No reply was written before the turn was interrupted.";
  return "No response. The turn ended without sending a message.";
}

function renderAssistantBody(m: ChatMsg, isLatest: boolean, fallbackModel: string, cardCtx: UiCardCtx) {
  // An error with no text is still an error; don't fall through to a blank bubble.
  if (m.meta?.error) return m.content.trim() || "The turn failed, but no error text was recorded.";
  const model = m.meta?.model ?? fallbackModel;
  if (m.meta?.segments && m.meta.segments.length) {
    return <AssistantTurn segments={m.meta.segments} isLatest={isLatest} model={model} cardCtx={cardCtx} />;
  }
  if (!m.content.trim()) return <span className="chat-noreply">{noReplyText(m)}</span>;
  return <AnswerBody source={m.content} cfg={modelDisplay(model)} expanded={isLatest} cardCtx={cardCtx} />;
}

export default function MessageList({
  messages, compactedThrough, clearedContext, selectedModel, cardCtx, expandLatest, onRetryUnsent,
}: {
  messages: ChatMsg[];
  compactedThrough: number;
  clearedContext: boolean;
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
  const cutMark = contextCutMark(compactedThrough, messages.length);

  return (
    <>
      {messages.map((m, i) => (
        <Fragment key={i}>
          {cutMark && "atIndex" in cutMark && i === cutMark.atIndex && (
            <ContextCut count={compactedThrough} cleared={clearedContext} />
          )}
          <div className={`chat-msg ${m.role}`}>
            {/* --copyable reserves the inline room the copy button occupies, so
                the button never lands on top of the last line of text. It is only
                set when there IS something to copy. */}
            <div className={`chat-bubble ${m.meta?.error ? "error" : ""}${(m.meta?.timeout || m.meta?.stopped) ? " chat-bubble--timeout" : ""}${m.content.trim() ? " chat-bubble--copyable" : ""}`}>
              <div className="chat-bubble__body">
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
                  {m.meta?.stopped && (
                    <div className="chat-timeout-tag">
                      <Unplug size={13} strokeWidth={1.8} aria-hidden /> Turn interrupted. Send a message to continue.
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
                      <span>Not sent. This never reached the server.</span>
                      <Button size="sm" variant="ghost" onClick={() => onRetryUnsent(i, m.content)}>Retry</Button>
                    </div>
                  )}
                </>
              )}
              </div>
              {m.content.trim() && <CopyMessage value={m.content} />}
            </div>
            {m.meta?.notes?.map((n, j) => (
              <div key={j} className="chat-note">{n}</div>
            ))}
            {m.meta?.status && <StatusNote status={m.meta.status} />}
          </div>
        </Fragment>
      ))}
      {/* The map above only draws the divider AT a message index. /clear always
          cuts at the tail — nothing there to attach to — so contextCutMark
          reports that case as "trailing" instead, and it renders here. */}
      {cutMark && "trailing" in cutMark && (
        <ContextCut count={compactedThrough} cleared={clearedContext} />
      )}
    </>
  );
}
