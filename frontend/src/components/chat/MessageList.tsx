import { Fragment, memo, useState } from "react";
import { ArrowRightLeft, Bot, Clock, Eraser, FileText, FoldVertical, Lightbulb, Radio, Ticket, TriangleAlert, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { chatApi, type ChatMsg } from "../../lib/chat";
import type { UiCardCtx } from "./uiCardActions";
import { AssistantTurn, AnswerBody, ElidedContent } from "./Transcript";
import CopyMessage from "./CopyMessage";
import LiveTurn from "./LiveTurn";
import StatusNote from "./StatusNote";
import UnsentNote from "./UnsentNote";
import { modelDisplay } from "./models";
import { stoppedTag } from "./stopReason";
import { hasBubble, noReplyText } from "./replyBody";
import type { TranscriptBlock } from "./transcriptBlocks";
import AttachmentViewer, { type ViewableAttachment } from "./AttachmentViewer";
import { receiptHref, receiptItems, receiptLabel } from "./receipts";

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

// One line, same divider language as ContextCut: a fact about the conversation
// itself, not a message in it, so it reads as a marker in the log rather than
// as something either side said.
function ScopeChangeNote({ to }: { to: string }) {
  return (
    <div className="chat-fold" role="separator">
      <ArrowRightLeft size={14} strokeWidth={1.8} aria-hidden />
      <span>Scope changed to {to}</span>
    </div>
  );
}

// WHAT THIS TURN CHANGED OUTSIDE THE CHAT: a note written, a ticket filed, a
// preference captured.
//
// A receipt is not a step and not a status, and the three used to render as the
// same anonymous `.chat-note` div, so "Saved a note to workshop" and "Stopped by
// operator" were indistinguishable rows. Steps fold away with the rest of a
// turn's work; a receipt does not, because when a turn is folded six messages
// back you still need to see that it wrote something.
//
// One row for the whole turn rather than one card per string. `meta.notes` is
// the pre-typing form and every stored turn has it, so it is read as untyped
// receipts rather than lost. See ADR 0015 §4.
// THE ICON NAMES THE OBJECT, IT DOES NOT CONGRATULATE THE MACHINE.
//
// Every receipt used to carry a green tick. A tick asserts "this succeeded",
// which is both redundant (an unqualified receipt is only written when the write
// went through) and uninformative: three receipts of three different kinds
// looked identical. It also spent the `ok` status hue on decoration, and status
// colour is a signal here, not an ornament. The icon now says WHAT was written,
// per the design system's rule that an icon names the thing beside it.
const RECEIPT_ICON: Record<string, typeof FileText> = {
  note: FileText,
  ticket: Ticket,
  learning: Lightbulb,
  action: Zap,
};

function Receipts({ msg }: { msg: ChatMsg }) {
  const items = receiptItems(msg.meta);
  if (!items.length) return null;
  return (
    <div className="chat-receipts">
      {items.map((r, j) => {
        const warn = r.kind === "warn";
        const Icon = warn ? TriangleAlert : (RECEIPT_ICON[r.kind] ?? FileText);
        const href = receiptHref(r);
        const body = (
          <>
            <Icon size={13} strokeWidth={1.8} aria-hidden />
            <span>{receiptLabel(r)}</span>
          </>
        );
        const cls = `chat-receipt chat-receipt--${warn ? "warn" : "ok"}`;
        return href
          ? <Link key={j} className={`${cls} chat-receipt--link`} to={href}>{body}</Link>
          : <span key={j} className={cls}>{body}</span>;
      })}
    </div>
  );
}

// How long the turn took, and ONLY when both ends are known.
//
// transcriptBlocks strips `ended_at` from every non-tail slice of a reply the
// operator interrupted, and leaves `started_at` on all of them. Computing a
// total from a half-pair would print the same wrong figure on each slice, and
// falling back to "now" would make five settled turns tick forever.
function turnTookMs(m: ChatMsg): number | undefined {
  const a = m.meta?.started_at;
  const b = m.meta?.ended_at;
  if (!a || !b) return undefined;
  const ms = Date.parse(b) - Date.parse(a);
  return Number.isFinite(ms) && ms > 0 ? ms : undefined;
}

function renderAssistantBody(m: ChatMsg, isLatest: boolean, fallbackModel: string, cardCtx: UiCardCtx,
                             answerPermission?: AnswerPermissionFor, nowMs?: number) {
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
        nowMs={nowMs}
        tookMs={turnTookMs(m)}
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

// What the platform posted, in three words, so the tier's label says which kind
// of unattended write this was rather than only that it was one.
function autonomousLabel(kind?: string): string {
  if (kind === "run_report") return "Autonomous run";
  if (kind === "ticket_question") return "Autonomous, needs you";
  return "Autonomous";
}

// Memoized per message. The list memo above already covers typing; this one
// covers a LIVE turn, where `elapsed` ticks once a second and re-renders the
// list by design — without it, every historical message re-rendered on every
// tick of the clock for the one turn at the bottom.
//
// All five props are stable per message: `msg` comes from the memoized blocks
// array, and the other four are shared and stable.
const MessageBubble = memo(function MessageBubble({
  msg, isLatest, selectedModel, cardCtx, answerPermission, nowMs,
}: {
  msg: ChatMsg;
  isLatest: boolean;
  selectedModel: string;
  cardCtx: UiCardCtx;
  answerPermission?: AnswerPermissionFor;
  // Only the LATEST turn can hold a running step, and only a running step reads
  // the clock, so this is 0 for every historical bubble and the memo above
  // keeps holding across a tick.
  nowMs?: number;
}) {
  const threadCfg = modelDisplay(selectedModel);
  // Stopping a turn before it wrote anything leaves nothing to put in a bubble,
  // and an empty box above the "user interrupted" line reads as a rendering
  // fault rather than as a turn the operator ended. See replyBody.hasBubble.
  const body = hasBubble(msg);
  // WHO PUT THIS HERE. A run report or a sweep's question was posted with
  // nobody watching, and that is the first thing a reader needs — so it takes
  // the autonomous agency tier (rail, tint, label) rather than passing itself
  // off as a reply to something you said. Everything else stays the neutral
  // card: tinting bubbles by tier turns a long log into a quilt.
  const autonomous = msg.role === "assistant" && !!msg.meta?.autonomous;
  return (
    <div className={`chat-msg ${msg.role}${autonomous ? " autonomous" : ""}`}
         {...(autonomous ? { "data-agency": "autonomous" as const } : {})}>
      {/* --copyable reserves the inline room the copy button occupies, so the
          button never lands on top of the last line of text. It is only set
          when there IS something to copy. */}
      {body && <div className={`chat-bubble ${msg.meta?.error ? "error" : ""}${msg.meta?.timeout ? " chat-bubble--timeout" : ""}${msg.content.trim() ? " chat-bubble--copyable" : ""}`}>
        <div className="chat-bubble__body">
          {/* Files the operator attached, shown on the message that carried
              them. Read-only here: the send already happened, so there is
              nothing to remove. */}
          {msg.role === "user" && !!msg.meta?.attachments?.length && (
            <SentAttachments items={msg.meta.attachments} />
          )}
          {msg.role === "assistant" ? (
            <>
              {autonomous && (
                <div className="chat-autonomous-tag">
                  <Radio size={12} strokeWidth={1.8} aria-hidden />
                  <span>{autonomousLabel(msg.meta?.kind)}</span>
                </div>
              )}
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
              {renderAssistantBody(msg, isLatest, selectedModel, cardCtx, answerPermission, nowMs)}
              {/* INSIDE the reply, as its last line. A receipt is part of what
                  the turn did, and rendering it as a sibling of the bubble made
                  a written note the one and only thing in this chat that got a
                  block of its own. It stays out of the work fold (a folded turn
                  must still show that it wrote something) but it belongs to the
                  message, not beside it. */}
              <Receipts msg={msg} />
            </>
          ) : (
            <ElidedContent source={msg.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
          )}
        </div>
        {msg.content.trim() && <CopyMessage value={msg.content} />}
      </div>}
      {/* One quiet line under the reply, and that is the whole treatment for a
          turn that ended early. No border, no tag above the body, no icon. */}
      {msg.meta?.stopped && (
        <div className="chat-status-line">{stoppedTag(msg.meta.stopped)}</div>
      )}
      {/* A turn stopped before it wrote anything has no bubble to sit in, and a
          receipt must never be dropped just because the reply was empty. */}
      {!body && <Receipts msg={msg} />}
      {msg.meta?.status && <StatusNote status={msg.meta.status} />}
    </div>
  );
});

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
/** The attachments on a message that has been sent. Same chip shape as the
 *  composer's, minus the remove control and the upload state: this is a record
 *  of what went, not a list you are still editing.
 *
 *  A BUTTON, not a link. This was `<a target="_blank">`, which opened the raw
 *  file in a new window: fine in a desktop tab, a dead end on a phone, where an
 *  installed app has no tab strip and no back gesture, so opening your own
 *  screenshot meant force-quitting the app to get back to the chat. It opens an
 *  overlay over the transcript now and the conversation keeps its place. */
function SentAttachments({ items }: {
  items: NonNullable<NonNullable<ChatMsg["meta"]>["attachments"]>;
}) {
  const [viewing, setViewing] = useState<ViewableAttachment | null>(null);
  return (
    <div className="chat-attach-row chat-attach-row--sent">
      {items.map((a) => (
        <button
          key={a.id}
          type="button"
          className="chat-attach is-sent"
          onClick={() => setViewing(a)}
          title={`Open ${a.name}`}
        >
          <span className="chat-attach__mark" aria-hidden>
            {a.kind === "image"
              ? <img className="chat-attach__thumb" src={chatApi.attachmentUrl(a.id)} alt="" />
              : <FileText size={14} strokeWidth={1.8} />}
          </span>
          <span className="chat-attach__name">{a.name}</span>
        </button>
      ))}
      {viewing && <AttachmentViewer file={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

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

// Memoized, and the reason is the composer. The chat's draft text is state on
// the same component that owns this transcript, so every keystroke re-renders
// the whole log; on a long thread that made typing on mobile unusable. Every
// prop below is already stable across a keystroke — `blocks`, `cardCtx`,
// `onRetry` and `answerPermission` are useMemo/useCallback in Chat.tsx and none
// of their deps involve the draft — so the shallow compare holds and the
// transcript is skipped entirely. `elapsed` and `workingLabel` do change once a
// second, but only while a run is live.
//
// Keep the props stable if you add one. An inline object or arrow here silently
// turns this memo off and nothing fails visibly; it just gets slow again.
function MessageList({
  blocks, selectedModel, cardCtx, workingLabel, elapsed, nowMs, onRetry,
  answerPermission, liveTurnId,
}: {
  blocks: TranscriptBlock[];
  selectedModel: string;
  cardCtx: UiCardCtx;
  workingLabel: string;
  elapsed: number;
  // The shared 1Hz clock. One interval for the whole transcript, already owned
  // by Chat.tsx and already gated on document.hidden; a per-step clock costs
  // nothing new because LiveTurn re-renders on the tick regardless.
  nowMs: number;
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
          {b.kind === "scope" && <ScopeChangeNote to={b.to} />}
          {b.kind === "message" && (
            <MessageBubble
              msg={b.msg}
              isLatest={b.key === latestAssistantKey}
              selectedModel={selectedModel}
              cardCtx={cardCtx}
              answerPermission={answerPermission}
              nowMs={b.key === latestAssistantKey ? nowMs : undefined}
            />
          )}
          {b.kind === "live" && (
            <LiveTurn
              segments={b.segments}
              working={b.working}
              workingLabel={workingLabel}
              elapsed={elapsed}
              nowMs={nowMs}
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

export default memo(MessageList);
