import { Fragment } from "react";
import { Bot, Clock, Eraser, FoldVertical } from "lucide-react";
import type { ChatMsg } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn, AnswerBody, ElidedContent } from "./Transcript";
import CopyMessage from "./CopyMessage";
import LiveTurn from "./LiveTurn";
import StatusNote from "./StatusNote";
import UnsentNote from "./UnsentNote";
import { modelDisplay } from "./models";
import { stoppedTag } from "./stopReason";
import type { TranscriptBlock } from "./transcriptBlocks";

// The transcript body: one rendered block per entry the selector produced.
//
// It renders a LIST OF BLOCKS, not a list of messages, and the difference is the
// point. An assistant reply that a mid-turn message interrupted is several
// blocks — the run of work before the message, the message, the run of work
// after it — because that is what actually happened. The order and the cuts are
// decided in transcriptBlocks.ts; nothing is arranged here.
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
//  - THE STOPPED LINE. A run that ended early is closed out with `meta.stopped`,
//    and it gets ONE quiet line under the reply saying which ending it was. It
//    briefly got the full treatment instead: a yellow border around the bubble,
//    a tagged warning above the body, and a sentence inside it, which is three
//    statements of one small fact and reads as an error the operator caused.
//    The line, and nothing else — see stopReason.ts.

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
  // A stopped turn says so ONCE, on the line under the reply. It used to say it
  // three times: a yellow border around the whole bubble, a tag above the body,
  // and this sentence. The operator asked for the line and nothing else.
  if (m.meta?.stopped) return "";
  return "No response. The turn ended without sending a message.";
}

function renderAssistantBody(m: ChatMsg, isLatest: boolean, fallbackModel: string, cardCtx: UiCardCtx,
                             answerPermission?: AnswerPermissionFor) {
  // An error with no text is still an error; don't fall through to a blank bubble.
  if (m.meta?.error) return m.content.trim() || "The turn failed, but no error text was recorded.";
  const model = m.meta?.model ?? fallbackModel;
  if (m.meta?.segments && m.meta.segments.length) {
    // Bound to the turn that ASKED. A persisted message carries its own
    // turn_id, which is the only way an approval answered after a reload names
    // the run it belongs to.
    const tid = m.meta.turn_id;
    return (
      <AssistantTurn
        segments={m.meta.segments} isLatest={isLatest} model={model} cardCtx={cardCtx}
        onAnswerPermission={
          answerPermission && tid ? (a) => answerPermission(tid, a) : undefined
        }
      />
    );
  }
  const noReply = noReplyText(m);
  if (!m.content.trim()) return noReply ? <span className="chat-noreply">{noReply}</span> : null;
  return <AnswerBody source={m.content} cfg={modelDisplay(model)} expanded={isLatest} cardCtx={cardCtx} />;
}

function MessageBubble({
  msg, isLatest, selectedModel, cardCtx, answerPermission,
}: {
  msg: ChatMsg;
  isLatest: boolean;
  selectedModel: string;
  cardCtx: UiCardCtx;
  answerPermission?: AnswerPermissionFor;
}) {
  const threadCfg = modelDisplay(selectedModel);
  return (
    <div className={`chat-msg ${msg.role}`}>
      {/* --copyable reserves the inline room the copy button occupies, so the
          button never lands on top of the last line of text. It is only set
          when there IS something to copy. */}
      <div className={`chat-bubble ${msg.meta?.error ? "error" : ""}${msg.meta?.timeout ? " chat-bubble--timeout" : ""}${msg.content.trim() ? " chat-bubble--copyable" : ""}`}>
        <div className="chat-bubble__body">
          {msg.role === "assistant" ? (
            <>
              {msg.meta?.agent && (
                <div className="chat-byline">
                  <Bot size={13} strokeWidth={1.8} aria-hidden />
                  <span>{msg.meta.agent}</span>
                </div>
              )}
              {msg.meta?.timeout && (
                <div className="chat-timeout-tag">
                  <Clock size={13} strokeWidth={1.8} aria-hidden /> Turn stopped at the time limit
                </div>
              )}
              {renderAssistantBody(msg, isLatest, selectedModel, cardCtx, answerPermission)}
            </>
          ) : (
            <ElidedContent source={msg.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
          )}
        </div>
        {msg.content.trim() && <CopyMessage value={msg.content} />}
      </div>
      {/* One quiet line under the reply, and that is the whole treatment for a
          turn that ended early. No border, no tag above the body, no icon. */}
      {msg.meta?.stopped && (
        <div className="chat-note">{stoppedTag(msg.meta.stopped)}</div>
      )}
      {msg.meta?.notes?.map((n, j) => (
        <div key={j} className="chat-note">{n}</div>
      ))}
      {msg.meta?.status && <StatusNote status={msg.meta.status} />}
    </div>
  );
}

// A message this browser has sent that the log does not carry yet.
//
// `sending`/`sent` render at the same reduced opacity the queue uses for "has
// not landed in the thread yet" — no badge, no delivery-state text. That was
// tried ("Handed to the turn / Read by the turn") and a pulsing line under a
// completely normal message is what made it LOOK stuck.
//
// `failed` is different and has to be: the turn never started, so the server has
// no copy of this message. A bubble that quietly is not real is exactly what
// "my messages keep disappearing" felt like from outside. It carries WHY, in a
// sentence, under the message it belongs to — see UnsentNote.
function PendingBubble({
  text, status, error, onRetry,
}: {
  text: string;
  status: "sending" | "sent" | "failed";
  error?: string;
  onRetry: () => void;
}) {
  const failed = status === "failed";
  return (
    <div className="chat-msg user">
      <div className={`chat-bubble ${failed ? "" : "chat-bubble--inflight"}${text.trim() ? " chat-bubble--copyable" : ""}`}>
        <div className="chat-bubble__body">
          {text}
          {failed && <UnsentNote reason={error} onRetry={onRetry} />}
        </div>
        {text.trim() && <CopyMessage value={text} />}
      </div>
    </div>
  );
}

// Bound by the caller to a THREAD; the turn id is supplied per call, because a
// transcript can hold approvals from several different runs.
export type AnswerPermissionFor = (
  turnId: string,
  a: { requestId: string; allow: boolean; message?: string; applySuggestion?: boolean },
) => Promise<void>;

export default function MessageList({
  blocks, selectedModel, cardCtx, workingLabel, elapsed, onRetry,
  answerPermission, liveTurnId,
}: {
  blocks: TranscriptBlock[];
  selectedModel: string;
  cardCtx: UiCardCtx;
  workingLabel: string;
  elapsed: number;
  onRetry: (clientId: string) => void;
  answerPermission?: AnswerPermissionFor;
  liveTurnId?: string | null;
}) {
  // Recency nuance: the most recent assistant answer is expanded and older ones
  // auto-shorten — but only when nothing is running, because a live turn is
  // itself the most recent thing.
  let latestAssistantKey: string | null = null;
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b.kind === "live") break;
    if (b.kind === "message" && b.msg.role === "assistant") { latestAssistantKey = b.key; break; }
  }

  return (
    <>
      {blocks.map((b) => (
        <Fragment key={b.key}>
          {b.kind === "cut" && <ContextCut count={b.count} cleared={b.cleared} />}
          {b.kind === "message" && (
            <MessageBubble
              msg={b.msg}
              isLatest={b.key === latestAssistantKey}
              selectedModel={selectedModel}
              cardCtx={cardCtx}
              answerPermission={answerPermission}
            />
          )}
          {b.kind === "live" && (
            <LiveTurn
              segments={b.segments}
              working={b.working}
              workingLabel={workingLabel}
              elapsed={elapsed}
              model={selectedModel}
              cardCtx={cardCtx}
              onAnswerPermission={
                answerPermission && liveTurnId ? (a) => answerPermission(liveTurnId, a) : undefined
              }
            />
          )}
          {b.kind === "pending" && (
            <PendingBubble
              text={b.text} status={b.status} error={b.error}
              onRetry={() => onRetry(b.clientId)}
            />
          )}
        </Fragment>
      ))}
    </>
  );
}
