import type { RefObject } from "react";
import type { MsgStatus } from "../../lib/chat";
import type { VoiceSnapshot } from "../../lib/voiceSession";
import type { UiCardCtx } from "./uiCardActions";
import type { TranscriptBlock } from "./transcriptBlocks";
import { logPlaceholder } from "./chatLogState";
import MessageList from "./MessageList";
import CopyMessage from "./CopyMessage";
import StatusNote from "./StatusNote";
import { SettlingTurn, VoiceLiveTurn } from "./VoiceTurn";

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
  selectedModel, cardCtx, onRetry, workingLabel, elapsed,
  queued, queuedStatus,
  voiceOn, settling, vsnap,
}: {
  scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  loading: boolean;
  error: string | null;
  blocks: TranscriptBlock[];
  isBuild: boolean;
  inFlight: boolean;
  selectedModel: string;
  cardCtx: UiCardCtx;
  onRetry: (clientId: string) => void;
  workingLabel: string;
  elapsed: number;
  queued: { text: string; sentAt: string; clientId: string }[];
  // Progress on a queued message, keyed by the id it will be sent with so it
  // stays under the entry it belongs to as other messages queue up behind it.
  queuedStatus: { for: string; status: MsgStatus } | null;
  voiceOn: boolean;
  settling: { user: string; assistant: string } | null;
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
      {placeholder === "loading" ? (
        <div className="chat-empty">Loading...</div>
      ) : placeholder === "error" ? (
        <div className="chat-empty chat-empty--err">
          Couldn't load threads. {String(error).slice(0, 240)}
        </div>
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
        onRetry={onRetry}
      />

      {queued.map((q) => (
        <div key={q.clientId} className="chat-msg user">
          <div
            className={`chat-bubble chat-bubble--queued${q.text.trim() ? " chat-bubble--copyable" : ""}`}
            title="Queued, sends when the current turn finishes"
          >
            <div className="chat-bubble__body">{q.text}</div>
            {q.text.trim() && <CopyMessage value={q.text} />}
          </div>
          {queuedStatus?.for === q.clientId && <StatusNote status={queuedStatus.status} />}
        </div>
      ))}

      {voiceOn && settling && <SettlingTurn settling={settling} />}
      {voiceLive && <VoiceLiveTurn vsnap={vsnap} />}
    </div>
  );
}
