import { useCallback, useState } from "react";
import { chatApi, type ChatMsg, type MsgStatus } from "../../lib/chat";
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
// EVERYTHING /compact SAYS, IT SAYS IN THE LOG. This used to report itself in
// the notice bar under the toolbar, which produced exactly the sequence the
// operator described: a banner pinned above the transcript for the ~30s the
// server-side fold takes, and only then a "/compact" bubble down in the chat —
// two announcements of one action, in two places, in the wrong order. Worse, a
// message sent during that window rendered ABOVE the banner in reading order,
// because a bar above the log has no position in time at all. The request is a
// bubble in the log from the instant it is made, and its progress is a status
// line under that bubble.
//
// THE RESULT IS NOT ANNOUNCED. The fold divider MessageList already draws from
// `compacted_through` says "N earlier messages folded into the running summary"
// and survives a reload; a second sentence saying the same thing in a different
// voice is exactly the duplication this chat had too much of. The status line
// only ever means "this is happening", and it clears when it stops happening.
//
// Not gated on `streaming`. Folding the stored transcript is a server-side
// operation on the saved thread, independent of whatever turn is in flight, and
// refusing while a turn ran meant /compact was rejected almost every time it was
// reached for — which is exactly when context is under pressure. Only the
// live-session half has to wait, and it queues rather than being dropped.
//
// ORDER IS THE CONTRACT. A /compact takes ~30s to fold server-side before its
// live half can even be sent, and three separate things used to let a message
// typed during that window overtake it:
//
//   a. the queue path was gated on `isBuild`, so an ordinary chat never took it;
//   b. the late hand-off did `drop the bubble, then queue.push`, and push
//      APPENDS — so the request re-entered the queue behind a message sent
//      after it, and rendered below it. That is the reordering the operator
//      saw: "my message got inserted above compact";
//   c. nothing marked the fold as busy, so a message typed during it went
//      straight out as its own turn while /compact was still in flight.
//
// (a) is gone, (b) is now insertAt with the position captured before the await,
// and (c) is `compacting` — lifted into the chat so the composer and the queue
// both hold off until the request has taken its turn.

const BUSY_NOW: MsgStatus = { text: "Compacting the conversation", busy: true };
const BUSY_QUEUED: MsgStatus = {
  text: "Compacting. The live session folds when the running turn finishes",
  busy: true,
};

export type Compaction = {
  compactedThrough: number;
  setCompactedThrough: (n: number) => void;
  // Bumped after a fold so the context meter refetches: folding does not change
  // messages.length, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
  // Set only while the request is sitting in the queue rather than in the log.
  queuedStatus: { for: string; status: MsgStatus } | null;
};

export function useCompaction({
  activeIdRef, logBusyRef, queue,
  setCompacting, setMessages, setErr, sendTurn,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  logBusyRef: React.MutableRefObject<boolean>;
  queue: MessageQueue;
  // Owned by the chat, not by this hook: the composer and the message queue both
  // have to hold while a fold is in flight, and both are declared before it.
  setCompacting: (v: boolean) => void;
  setMessages: React.Dispatch<React.SetStateAction<ChatMsg[]>>;
  setErr: (m: string | null) => void;
  sendTurn: (text: string) => void;
}): Compaction {
  const [compactedThrough, setCompactedThrough] = useState(0);
  const [ctxNonce, setCtxNonce] = useState(0);
  const [queuedStatus, setQueuedStatus] = useState<{ for: string; status: MsgStatus } | null>(null);

  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr("Nothing to compact — this chat has no messages yet."); return; }
    setErr(null);
    setCompacting(true);

    // The request goes into the log NOW, at the moment it was made, carrying its
    // own progress. `ts` identifies it so the no-op paths can take this exact
    // bubble back out again.
    const requestTs = new Date().toISOString();
    const isRequest = (m: ChatMsg) => m.ts === requestTs && m.content === "/compact";
    const setStatus = (status?: MsgStatus) =>
      setMessages((m) => m.map((x) => (isRequest(x) ? { ...x, meta: { ...x.meta, status } } : x)));
    const dropRequestBubble = () => setMessages((m) => m.filter((x) => !isRequest(x)));

    // Where in the queue this request belongs, captured NOW. Everything typed
    // while the fold is in flight goes behind this index, so the command keeps
    // the position the operator gave it even though it is handed over late.
    const queuePos = queue.length;

    // Queue the live-session half UP FRONT when a turn is already running. It
    // used to be queued only after the server fold returned — seconds of round
    // trip during which the running turn kept streaming — so the "/compact"
    // bubble landed below a wall of output that arrived after it was asked for,
    // and read as though the request had been ignored and picked up at random.
    let preQueued = logBusyRef.current;
    if (preQueued) {
      queue.push("/compact");
      setQueuedStatus({ for: "/compact", status: BUSY_QUEUED });
    } else {
      setMessages((m) => [...m, {
        role: "user", content: "/compact", ts: requestTs, meta: { status: BUSY_NOW },
      }]);
    }

    try {
      const r = await chatApi.compactThread(id);
      setCompactedThrough(r.compacted_through);
      setCtxNonce((n) => n + 1);

      if (r.live_session) {
        if (!preQueued) {
          if (logBusyRef.current) {
            // A turn started while the fold was in flight. Hand it to the queue,
            // which renders its own pending bubble — so drop ours or the request
            // shows twice.
            dropRequestBubble();
            preQueued = true;
            queue.insertAt(queuePos, "/compact");   // invariant 5, not push
            setQueuedStatus({ for: "/compact", status: BUSY_QUEUED });
          } else {
            // The live half runs as an ordinary turn: its reply streams in below
            // this bubble and the working indicator reads "Compacting", so this
            // line's job is done.
            setStatus(undefined);
            sendTurn("/compact");
          }
        }
      } else {
        // No live session after all — take the speculative entry back out rather
        // than letting a literal "/compact" reach a model as text.
        if (preQueued) {
          queue.drop("/compact");
          setQueuedStatus(null);
        }
        if (r.folded === 0) {
          // Nothing happened, so nothing should be left claiming it did.
          dropRequestBubble();
          setErr("Nothing to compact — the transcript is already short.");
        } else {
          // The fold divider is the report; this line has nothing left to add.
          setStatus(undefined);
        }
      }
    } catch (e) {
      if (preQueued) {
        queue.drop("/compact");
        setQueuedStatus(null);
      }
      dropRequestBubble();
      setErr(`Couldn't compact: ${String(e)}`);
    } finally {
      // Released only here: until it clears, the composer queues rather than
      // sends, so nothing typed during the fold can become a turn ahead of the
      // /compact that is still on its way out.
      setCompacting(false);
    }
  }, [activeIdRef, logBusyRef, queue, setCompacting, setMessages, setErr, sendTurn]);

  return { compactedThrough, setCompactedThrough, ctxNonce, compact, queuedStatus };
}
