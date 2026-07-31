import { useCallback, useState } from "react";
import { chatApi, type ChatMsg } from "../../lib/chat";
import type { ChatNotice } from "./ChatNotice";
import type { MessageQueue } from "./useMessageQueue";

// /compact — reclaim context now.
//
// A CHAT'S CONTEXT LIVES IN TWO PLACES, which is why compacting either one alone
// never visibly did anything:
//
//   1. the STORED TRANSCRIPT, re-assembled whenever a fresh session starts →
//      folded server-side by POST /threads/:id/compact.
//   2. the LIVE CLAUDE SESSION, whose window is what actually fills up → only
//      the CLI's own /compact frees that, and it does so by running as a turn.
//
// Running (2) as a normal turn is deliberate: the fold then appears in the
// transcript with a reply, instead of being an invisible state change.
//
// Not gated on `streaming`. Folding the stored transcript is a server-side
// operation on the saved thread, independent of whatever turn is in flight, and
// refusing while a turn ran meant /compact was rejected almost every time it was
// reached for — which is exactly when context is under pressure. Only the
// live-session half has to wait, and it queues rather than being dropped.

export type Compaction = {
  compacting: boolean;
  compactedThrough: number;
  setCompactedThrough: (n: number) => void;
  // Bumped after a fold so the context meter refetches: folding does not change
  // messages.length, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
};

export function useCompaction({
  activeIdRef, streamingRef, isBuild, queue,
  setMessages, setNotice, setErr, sendTurn,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  streamingRef: React.MutableRefObject<boolean>;
  isBuild: boolean;
  queue: MessageQueue;
  setMessages: React.Dispatch<React.SetStateAction<ChatMsg[]>>;
  setNotice: (n: ChatNotice | null) => void;
  setErr: (m: string | null) => void;
  sendTurn: (text: string) => void;
}): Compaction {
  const [compacting, setCompacting] = useState(false);
  const [compactedThrough, setCompactedThrough] = useState(0);
  const [ctxNonce, setCtxNonce] = useState(0);

  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr("Nothing to compact — this chat has no messages yet."); return; }
    setErr(null);
    setCompacting(true);

    // Queue the live-session half UP FRONT when a turn is already running. It
    // used to be queued only after the server fold returned — seconds of round
    // trip during which the running turn kept streaming — so the "/compact"
    // bubble landed below a wall of output that arrived after it was asked for,
    // and read as though the request had been ignored and picked up at random.
    const preQueued = streamingRef.current && isBuild;
    if (preQueued) queue.push("/compact");

    // Put the request in the log at the moment it was made. `ts` is kept so the
    // no-op paths can take this exact bubble back out again.
    const requestTs = new Date().toISOString();
    if (!preQueued && !streamingRef.current) {
      setMessages((m) => [...m, { role: "user", content: "/compact", ts: requestTs }]);
    }
    const dropRequestBubble = () =>
      setMessages((m) => m.filter((x) => !(x.ts === requestTs && x.content === "/compact")));

    // Without a note here the only feedback is the finished result, so a slow
    // (or failed) pass is indistinguishable from a button that did nothing.
    setNotice({
      kind: "compaction",
      busy: true,
      text: preQueued
        ? "Compacting. The live session folds when the running turn finishes."
        : "Compacting the conversation…",
    });

    try {
      const r = await chatApi.compactThread(id);
      setCompactedThrough(r.compacted_through);
      setCtxNonce((n) => n + 1);
      if (r.live_session) {
        if (!preQueued) {
          if (streamingRef.current) {
            // A turn started while the fold was in flight. Hand it to the queue,
            // which renders its own pending bubble — so drop ours or the request
            // shows twice.
            dropRequestBubble();
            queue.push("/compact");
          } else {
            sendTurn("/compact");
          }
          setNotice(null);
        }
      } else {
        // No live session after all — take the speculative entry back out rather
        // than letting a literal "/compact" reach a model as text.
        if (preQueued) queue.drop("/compact");
        if (r.folded === 0) {
          // Nothing happened, so nothing should be left claiming it did.
          dropRequestBubble();
          setNotice(null);
          setErr("Nothing to compact — the transcript is already short.");
        } else {
          setNotice({
            kind: "compaction",
            text: `Compacted ${r.folded} messages into the running summary.`,
          });
        }
      }
    } catch (e) {
      if (preQueued) queue.drop("/compact");
      dropRequestBubble();
      setNotice(null);
      setErr(`Couldn't compact: ${String(e)}`);
    } finally {
      setCompacting(false);
    }
  }, [activeIdRef, streamingRef, isBuild, queue, setMessages, setNotice, setErr, sendTurn]);

  return { compacting, compactedThrough, setCompactedThrough, ctxNonce, compact };
}
