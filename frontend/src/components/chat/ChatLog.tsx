import type { RefObject } from "react";
import type { ChatMsg, ChatSegment, MsgStatus } from "../../lib/chat";
import type { VoiceSnapshot } from "../../lib/voiceSession";
import type { UiCardCtx } from "./uiCardActions";
import MessageList from "./MessageList";
import StatusNote from "./StatusNote";
import LiveTurn from "./LiveTurn";
import { SettlingTurn, VoiceLiveTurn } from "./VoiceTurn";

// The scrolling transcript, in the order things actually happened:
//
//   persisted messages -> the live turn -> queued messages -> the spoken turn
//
// That order is not arbitrary. Queued bubbles sit BELOW the live turn because
// they have not been sent yet; the settling voice exchange sits above the live
// voice overlay so a fast follow-up never hides the turn that just finished.
//
// Scroll behaviour is not here — it belongs to useLogScroll, which owns the one
// writer of "am I at the bottom" and every rule that depends on it.

export default function ChatLog({
  scrollRef, onScroll,
  loading, error, messages, isBuild, inFlight, overflowed,
  compactedThrough, selectedModel, cardCtx, onRetryUnsent,
  streaming, logBusy, pendingSegments, interjections, workingLabel, elapsed,
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
  selectedModel: string;
  cardCtx: UiCardCtx;
  onRetryUnsent: (index: number, text: string) => void;
  streaming: boolean;
  // A turn owns the tail of the log: streaming, or still settling its reply
  // into `messages`. Computed once in Chat so this condition cannot drift
  // apart from the one the send path uses to decide queue-vs-append.
  logBusy: boolean;
  pendingSegments: ChatSegment[];
  interjections: { at: number; text: string }[];
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
          Showing the latest messages. Older history is paginated server-side.
        </div>
      )}

      <MessageList
        messages={messages}
        compactedThrough={compactedThrough}
        selectedModel={selectedModel}
        cardCtx={cardCtx}
        expandLatest={!streaming && pendingSegments.length === 0}
        onRetryUnsent={onRetryUnsent}
      />

      {logBusy && (
        <LiveTurn
          segments={pendingSegments}
          interjections={interjections}
          streaming={streaming}
          workingLabel={workingLabel}
          elapsed={elapsed}
          model={selectedModel}
          cardCtx={cardCtx}
        />
      )}

      {queued.map((q, i) => (
        <div key={`q${i}`} className="chat-msg user">
          <div className="chat-bubble chat-bubble--queued" title="Queued, sends when the current turn finishes">
            {q.text}
          </div>
          {queuedStatus?.for === q.text && <StatusNote status={queuedStatus.status} />}
        </div>
      ))}

      {voiceOn && settling && <SettlingTurn settling={settling} />}
      {voiceLive && <VoiceLiveTurn vsnap={vsnap} />}
    </div>
  );
}
