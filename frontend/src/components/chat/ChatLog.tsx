import type { RefObject } from "react";
import type { ChatMsg, ChatSegment, MsgStatus } from "../../lib/chat";
import type { VoiceSnapshot } from "../../lib/voiceSession";
import type { UiCardCtx } from "./uiCardActions";
import MessageList from "./MessageList";
import CopyMessage from "./CopyMessage";
import StatusNote from "./StatusNote";
import LiveTurn from "./LiveTurn";
import { SettlingTurn, VoiceLiveTurn } from "./VoiceTurn";

// The scrolling transcript, APPEND-ONLY AND IN SEND ORDER:
//
//   persisted messages
//     -> the live turn (its working indicator at the tail)
//     -> messages sent DURING that turn
//     -> messages queued for the next turn
//     -> the spoken turn
//
// That order is the ordinary chat rule and it is not arbitrary. A message typed
// while a reply is being written goes BELOW that reply and below its working
// indicator: the work in flight was started without it, so nothing about it can
// come first. This replaced a scheme that cut the live reply open and rendered
// the message inside the gap — which put the operator's words above work that
// had already begun, and was wrong in exactly the way they said it was.
//
// Queued bubbles sit below those again because they have not been sent at all
// yet; the settling voice exchange sits above the live voice overlay so a fast
// follow-up never hides the turn that just finished.
//
// Scroll behaviour is not here — it belongs to useLogScroll, which owns the one
// writer of "am I at the bottom" and every rule that depends on it.

export default function ChatLog({
  scrollRef, onScroll,
  loading, error, messages, isBuild, inFlight, overflowed,
  compactedThrough, clearedContext, selectedModel, cardCtx, onRetryUnsent,
  streaming, logBusy, pendingSegments, sentDuringTurn, workingLabel, elapsed,
  queued, queuedStatus,
  voiceOn, settling, vsnap,
}: {
  scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  loading: boolean;
  error: string | null;
  messages: ChatMsg[];
  isBuild: boolean;
  inFlight: boolean;
  overflowed: boolean;
  compactedThrough: number;
  // Whether the boundary at compactedThrough was made by /clear (no summary)
  // rather than a fold (one survives). MessageList's divider is the only reader.
  clearedContext: boolean;
  selectedModel: string;
  cardCtx: UiCardCtx;
  onRetryUnsent: (index: number, text: string) => void;
  streaming: boolean;
  // A turn owns the tail of the log: streaming, or still settling its reply
  // into `messages`. Computed once in Chat so this condition cannot drift
  // apart from the one the send path uses to decide queue-vs-append.
  logBusy: boolean;
  pendingSegments: ChatSegment[];
  // Sent while the live turn was already running. Rendered below it, in the
  // order sent, as ordinary messages.
  sentDuringTurn: { text: string }[];
  workingLabel: string;
  elapsed: number;
  queued: { text: string; sentAt: string }[];
  // Progress on a queued message, keyed by its text so it stays under the entry
  // it belongs to as other messages queue up behind it.
  queuedStatus: { for: string; status: MsgStatus } | null;
  voiceOn: boolean;
  settling: { user: string; assistant: string } | null;
  vsnap: VoiceSnapshot;
}) {
  const empty = messages.length === 0;
  const voiceLive = voiceOn && (vsnap.state === "thinking" || vsnap.state === "speaking");

  return (
    <div className="chat-log" ref={scrollRef} onScroll={onScroll}>
      {loading && empty ? (
        <div className="chat-empty">Loading...</div>
      ) : error && empty ? (
        <div className="chat-empty chat-empty--err">
          Couldn't load threads. {String(error).slice(0, 240)}
        </div>
      ) : empty && !inFlight ? (
        <div className="chat-empty">
          {isBuild ? "Tell the agent what to build..." : "Ask anything, or tell me to change the plan."}
        </div>
      ) : null}

      {overflowed && (
        <div className="chat-empty chat-empty--note">
          Showing the latest messages. Scroll up to load older ones.
        </div>
      )}

      <MessageList
        messages={messages}
        compactedThrough={compactedThrough}
        clearedContext={clearedContext}
        selectedModel={selectedModel}
        cardCtx={cardCtx}
        expandLatest={!streaming && pendingSegments.length === 0}
        onRetryUnsent={onRetryUnsent}
      />

      {logBusy && (
        <LiveTurn
          segments={pendingSegments}
          streaming={streaming}
          workingLabel={workingLabel}
          elapsed={elapsed}
          model={selectedModel}
          cardCtx={cardCtx}
        />
      )}

      {/* Sent during the turn above, so they render below it. No badge, no
          label, no delivery-state text — that was tried ("Handed to the turn
          / Read by the turn"), and a pulsing line under a completely normal
          message was what made it LOOK stuck, which is the opposite of what
          it was for. What it stays is a plain, static, ordinary-chat cue:
          --chat-bubble--inflight is the exact opacity `--queued` already
          uses below for "hasn't landed in the thread yet" — no new state, no
          text, nothing that can pulse or look broken. It clears itself: this
          array is only ever non-empty while logBusy is true, so a message
          sitting here goes full-color the moment the turn actually ends and
          it folds into the persisted transcript. */}
      {sentDuringTurn.map((m, i) => (
        <div key={`d${i}`} className="chat-msg user">
          {/* A pending message is a message: copyable like everything else,
              same bubble structure MessageList uses so the copy button reserves
              its inline room and lands on the last row. */}
          <div className={`chat-bubble chat-bubble--inflight${m.text.trim() ? " chat-bubble--copyable" : ""}`}>
            <div className="chat-bubble__body">{m.text}</div>
            {m.text.trim() && <CopyMessage value={m.text} />}
          </div>
        </div>
      ))}

      {queued.map((q, i) => (
        <div key={`q${i}`} className="chat-msg user">
          <div
            className={`chat-bubble chat-bubble--queued${q.text.trim() ? " chat-bubble--copyable" : ""}`}
            title="Queued, sends when the current turn finishes"
          >
            <div className="chat-bubble__body">{q.text}</div>
            {q.text.trim() && <CopyMessage value={q.text} />}
          </div>
          {queuedStatus?.for === q.text && <StatusNote status={queuedStatus.status} />}
        </div>
      ))}

      {voiceOn && settling && <SettlingTurn settling={settling} />}
      {voiceLive && <VoiceLiveTurn vsnap={vsnap} />}
    </div>
  );
}
