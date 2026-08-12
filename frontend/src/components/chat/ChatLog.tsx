import type { RefObject } from "react";
import type { VoiceSnapshot } from "../../lib/voice/client";
import type { UiCardCtx } from "./uiCardActions";
import type { TranscriptBlock } from "./transcriptBlocks";
import { logPlaceholder } from "./chatLogState";
import type { ChatProblem } from "./chatProblem";
import ChatProblemBlock from "./ChatProblemBlock";
import MessageList, { SentAttachments, type AnswerPermissionFor } from "./MessageList";
import CopyMessage from "./CopyMessage";
import QueueNote from "./QueueNote";
import { SettlingTurn, VoiceLiveTurn, VoicePendingTurn } from "./VoiceTurn";

// The scrolling transcript. It renders BLOCKS the selector produced, in the
// order it produced them, and arranges nothing itself.
//
// ---------------------------------------------------------------------------
// THE ORDER RULE, and why it changed
// ---------------------------------------------------------------------------
//
// A message sent while a reply is being written CLOSES that reply's block. The
// work already emitted stays above it; work produced afterwards starts a new
// block below it. So a long turn the operator interrupted five times renders as
// six stretches of work with the five messages between them, each one sitting
// where it actually landed.
//
// This file used to say the opposite, and the history matters because both of
// the earlier rules were responses to real corrections:
//
//   1. The first scheme cut the reply open and put the message in the gap — but
//      positioned it ABOVE work that had already begun (and, when nothing had
//      streamed yet, above the working indicator itself). Wrong: the work in
//      flight was started without that message, so nothing about it can precede
//      that work.
//   2. The fix was "append-only, in send order": the message renders BELOW the
//      whole reply. That got the first case right and made the common case
//      wrong — the reply that finally answered five messages rendered above all
//      five, which reads exactly like being ignored, and is what the operator
//      reported.
//
// Cutting at `meta.at_segment` is neither. It is the index into the reply's
// segment stream at which the message arrived, recorded by the server off the
// LIVE run, so the message lands after what had been emitted and before what
// had not. The rule now lives in transcriptBlocks.ts, where it is tested.
//
// Queued bubbles sit below everything because they have not been sent at all
// yet; the settling voice exchange sits above the live voice overlay so a fast
// follow-up never hides the turn that just finished.
//
// Scroll behaviour is not here — it belongs to useLogScroll, which owns the one
// writer of "am I at the bottom" and every rule that depends on it.
//
// THERE IS NO OVERFLOW STATE, because there is no overflow. Past 500 messages
// this used to say "Showing the latest messages. Scroll up to load older ones."
// The client fetches `events?after=0` with no limit and there is no loader to
// scroll up into: every message was already on screen, and the note described a
// paging scheme that does not exist. If one is ever built, the banner comes back
// with it.

export default function ChatLog({
  scrollRef, onScroll,
  loading, error, blocks, isBuild, inFlight,
  selectedModel, cardCtx, onRetry, onCancelQueued, onReleaseQueue,
  queueHeld, queueError, workingLabel, elapsed, nowMs,
  answerPermission, liveTurnId,
  queued,
  voiceOn, settling, vsnap,
}: {
  scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  loading: boolean;
  /** The chat's current chat-scoped failure. The log owns the ERROR one of its
   *  four list states, so when there is nothing else to show this is where that
   *  failure is rendered; the host suppresses its own copy. */
  error: ChatProblem | null;
  blocks: TranscriptBlock[];
  isBuild: boolean;
  inFlight: boolean;
  selectedModel: string;
  cardCtx: UiCardCtx;
  onRetry: (clientId: string) => void;
  /** Unsend a message the SERVER is holding. It is the operator's text and
   *  taking it back before it goes is theirs to do. */
  onCancelQueued: (clientId: string) => void;
  /** Send what is waiting: after a Stop, or after a drain that failed. */
  onReleaseQueue: () => void;
  /** Stop is holding the queue. Nothing goes until the operator says so. */
  queueHeld: boolean;
  /** Why the queue is not moving, when it stopped for a reason. */
  queueError: string;
  workingLabel: string;
  elapsed: number;
  // The shared 1Hz clock, threaded to the per-step counters in the live turn.
  nowMs: number;
  answerPermission?: AnswerPermissionFor;
  // The run in flight, so a pending approval on it can be answered. Persisted
  // messages carry their own turn id; a live one has not been committed yet.
  liveTurnId?: string | null;
  /** Messages the SERVER is holding to send next. Read, never written: this is
   *  the thread's own queue, the same on every device. */
  queued: {
    text: string; sentAt: string; clientId: string;
    attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
  }[];
  voiceOn: boolean;
  settling: { user: string; assistant: string } | null;
  vsnap: VoiceSnapshot;
}) {
  const voiceLive = voiceOn && (vsnap.state === "thinking" || vsnap.state === "speaking");
  // The utterance being spoken right now, as a prospective bubble at the foot of
  // the log. Only while listening: once the turn commits, `lastUser` in
  // VoiceLiveTurn owns it and rendering both would print it twice.
  const voicePending = voiceOn && vsnap.state === "listening" && !!(vsnap.partial || vsnap.pendingUser);
  // `loading` covers the thread list AND this thread's first page, so the empty
  // state cannot be reached before the log has actually arrived. The rule is in
  // chatLogState.ts, where it is tested.
  const placeholder = logPlaceholder({
    empty: blocks.length === 0, loading, error: !!error, inFlight,
  });

  return (
    <div className="chat-log" ref={scrollRef} onScroll={onScroll}>
      {placeholder === "loading" ? (
        <div className="chat-empty">Loading...</div>
      ) : placeholder === "error" && error ? (
        // It used to say "Couldn't load threads." in front of the real message,
        // which was a guess: the same slot carried a failed poll, a failed model
        // list and a failed delete, and only one of those was about threads.
        <ChatProblemBlock problem={error} />
      ) : placeholder === "empty" ? (
        <div className="chat-empty">
          {isBuild ? "Tell the agent what to build..." : "Ask anything, or tell me to change the plan."}
        </div>
      ) : null}

      <MessageList
        blocks={blocks}
        selectedModel={selectedModel}
        cardCtx={cardCtx}
        workingLabel={workingLabel}
        elapsed={elapsed}
        nowMs={nowMs}
        onRetry={onRetry}
        answerPermission={answerPermission}
        liveTurnId={liveTurnId}
      />

      {/* WAITING ON THE SERVER, not in this browser. These have been accepted
          and are on the thread; they go out when the run in flight ends, sent
          by the server, whether or not this tab is still open. So the line
          under them is a statement about the system, not a promise this page
          is making on its own. */}
      {queued.map((q) => (
        <div key={q.clientId} className="chat-msg user">
          <div
            className={`chat-bubble chat-bubble--queued${q.text.trim() ? " chat-bubble--copyable" : ""}`}
          >
            <div className="chat-bubble__body">
              {/* A queued message keeps its files visible too. They are already
                  uploaded, so the only thing waiting is the turn. */}
              {!!q.attachments?.length && <SentAttachments items={q.attachments} />}
              {q.text}
            </div>
            {q.text.trim() && <CopyMessage value={q.text} />}
          </div>
        </div>
      ))}
      {queued.length > 0 && (
        <QueueNote
          count={queued.length}
          held={queueHeld}
          error={queueError}
          onRelease={onReleaseQueue}
          onCancelLast={() => onCancelQueued(queued[queued.length - 1].clientId)}
        />
      )}

      {voiceOn && settling && <SettlingTurn settling={settling} />}
      {voiceLive && <VoiceLiveTurn vsnap={vsnap} />}
      {voicePending && <VoicePendingTurn text={vsnap.partial || vsnap.pendingUser} previewing={vsnap.previewing} />}
    </div>
  );
}
