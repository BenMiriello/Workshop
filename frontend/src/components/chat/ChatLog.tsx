import type { ReactNode, RefObject } from "react";
import type { VoiceSnapshot } from "../../lib/voice/client";
import type { UiCardCtx } from "./uiCardActions";
import type { TranscriptBlock } from "./transcriptBlocks";
import { logPlaceholder } from "./chatLogState";
import type { ChatProblem } from "./chatProblem";
import ChatProblemBlock from "./ChatProblemBlock";
import LoadOlderNote from "./LoadOlderNote";
import MessageList, { SentAttachments, type AnswerPermissionFor } from "./MessageList";
import FoldingNote from "./FoldingNote";
import type { FoldMode } from "./useCompaction";
import CopyMessage from "./CopyMessage";
import QueueNote from "./QueueNote";
import { VoiceReply } from "./VoiceTurn";

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
// yet, and the voice overlay sits below them. That overlay is now the REPLY
// only: a spoken message is written to the thread when it is captured, so it is
// an ordinary message in the list above, and what is still being said is a draft
// in the composer. Nothing here renders the operator's own words.
//
// Scroll behaviour is not here — it belongs to useLogScroll, which owns the one
// writer of "am I at the bottom" and every rule that depends on it.
//
// THE OVERFLOW STATE IS AT THE TOP, NOW. Opening a thread fetches only its
// newest slice (`INITIAL_TAIL` in useThreadLog), not the whole log, so a long
// conversation has history above the fold this tab has not asked for yet.
// `LoadOlderNote` is that state: shown only when the server says there is
// more (`hasMoreOlder`), it pages one more window into the past and PREPENDS
// it, preserving where the operator was reading (useLogScroll's
// `holdForOlder`). Everything below this point in the file is unchanged by
// that: it renders `blocks` in the order the selector produced them, and the
// selector's rules (docs/decisions/0018) hold over a partial window exactly
// as they do over the whole log — a cut whose other half is not (yet) loaded
// just does not split there, which is the honest answer to "we do not have
// that part", not a special case.

export default function ChatLog({
  scrollRef, onScroll,
  loading, error, blocks, isBuild, inFlight,
  selectedModel, cardCtx, onRetry, onCancelQueued, onReleaseQueue,
  queueHeld, queueError, workingLabel, elapsed, nowMs,
  answerPermission, liveTurnId, renderBranch, branchHead,
  queued, folding, onStop, stopping, onStopFold, foldStopping,
  voiceOn, vsnap,
  hasMoreOlder, loadingOlder, olderError, onLoadOlder,
}: {
  scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  loading: boolean;
  /** The chat's current chat-scoped failure. The log owns the ERROR one of its
   *  four list states, so when there is nothing else to show this is where that
   *  failure is rendered; the host suppresses its own copy. */
  error: ChatProblem | null;
  /** The server says there is more history above what is loaded. Drives
   *  whether `LoadOlderNote` shows at all. */
  hasMoreOlder: boolean;
  /** A `loadOlder` page is in flight. */
  loadingOlder: boolean;
  /** The last `loadOlder` page failed, in a sentence, or null. */
  olderError: string | null;
  onLoadOlder: () => void;
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
  /** Builds the "go back to here" control for one message. Passed straight
   *  through to MessageList; absent when this chat cannot be branched. */
  renderBranch?: (messageId: string, mode: "after" | "before") => ReactNode;
  /** The branch's last committed message, so the list can tell which bubble the
   *  action would be a no-op on. Passed straight through. */
  branchHead?: string | null;
  /** Messages the SERVER is holding to send next. Read, never written: this is
   *  the thread's own queue, the same on every device. */
  queued: {
    text: string; sentAt: string; clientId: string;
    attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
  }[];
  /** A /compact or /clear in flight, or null. It is drawn at the foot of the
   *  log, where its divider lands, and nowhere else. */
  folding: FoldMode | null;
  /** End the run in flight. Rendered on the working line, inside the transcript,
   *  rather than under the composer: the control belongs beside the work. */
  onStop?: () => void;
  /** The stop request is out; the working line's control shows it. */
  stopping?: boolean;
  /** Abandon the fold in flight. Same idea, on the fold's own line. */
  onStopFold?: () => void;
  /** That cancel is out; the fold line's control shows it. */
  foldStopping?: boolean;
  voiceOn: boolean;
  vsnap: VoiceSnapshot;
}) {
  const voiceLive = voiceOn && (vsnap.state === "thinking" || vsnap.state === "speaking");
  // `loading` covers the thread list AND this thread's first page, so the empty
  // state cannot be reached before the log has actually arrived. The rule is in
  // chatLogState.ts, where it is tested.
  const placeholder = logPlaceholder({
    empty: blocks.length === 0, loading, error: !!error, inFlight,
  });

  return (
    <div className="chat-log" ref={scrollRef} onScroll={onScroll}>
      {/* Above everything, including the loading/empty/error placeholder: it
          answers a different question ("is there more before what you can
          see") than that one does ("has what you can see arrived"), and both
          can be true in a long thread that failed to page further back. */}
      {(hasMoreOlder || loadingOlder || !!olderError) && (
        <LoadOlderNote loading={loadingOlder} error={olderError} onLoad={onLoadOlder} />
      )}
      {placeholder === "loading" ? (
        <div className="chat-empty">Loading...</div>
      ) : placeholder === "error" && error ? (
        // It used to say "Couldn't load threads." in front of the real message,
        // which was a guess: the same slot carried a failed poll, a failed model
        // list and a failed delete, and only one of those was about threads.
        // The region shape, and one of the two places in the chat that earns
        // it: this IS the log's four-states error, standing where the
        // transcript would be. Everywhere else the transcript is fine and the
        // failure costs a line.
        <ChatProblemBlock problem={error} shape="region" />
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
        renderBranch={renderBranch}
        branchHead={branchHead}
        onStop={onStop}
        stopping={stopping}
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

      {/* The fold, while it runs. Below the queue for the same reason the cut
          divider ends up below it: what is queued was typed before the fold was
          asked for, and the boundary lands after everything above it. */}
      {folding && <FoldingNote mode={folding} onStop={onStopFold} stopping={foldStopping} />}

      {/* ONE voice element, and it renders the REPLY only.
          What the operator is saying is in the composer, as a draft (see
          voiceDictation.ts); what he has said is an ordinary message in the log
          above, written when it was captured. There used to be three siblings
          here, each drawing his words from a different field on a different
          phase check, plus a held copy of the last exchange. Three handoffs a
          turn, by timing rather than identity, is where the flicker, the
          duplication and the words landing in the previous bubble came from. */}
      {voiceLive && <VoiceReply vsnap={vsnap} />}
    </div>
  );
}
