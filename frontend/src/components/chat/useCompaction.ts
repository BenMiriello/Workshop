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
// THERE IS NO LONGER A GAP TO FALL INTO. The two halves used to be SERIALIZED:
// the client awaited the server fold — a summarization call, tens of seconds —
// and only then sent the live half. That is precisely what the operator kept
// reporting: "it shows a message saying compacting... and then 30s later it
// starts ACTUALLY compacting." Nothing in the live half depended on the stored
// one, so the wait bought nothing and cost the whole delay. The endpoint now
// answers immediately with the two facts this hook needs (is there a live
// session, how many turns will fold) and summarizes detached.
//
// ORDER IS STILL THE CONTRACT, because a turn can already be running when
// /compact is asked for. Three separate things used to let a message typed
// during that window overtake it:
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

// There is no "compacting now" status any more. It existed to cover the ~30s the
// server fold used to take, and covering a wait is its only job — now that the
// request turns into a running turn in milliseconds, it would flash and vanish.
// That flash is what the operator described as "a weird one-off message, not in
// a bubble, on the user side, saying compacting". While the turn runs, the live
// indicator already reads "Compacting", which is one statement in one place.
//
// The queued wait is REAL — it lasts until the running turn finishes — so it
// keeps its line.
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

    // The request goes into the log NOW, at the moment it was made — not when
    // the server answers. `ts` identifies it so the no-op paths can take this
    // exact bubble back out again.
    const requestTs = new Date().toISOString();
    const isRequest = (m: ChatMsg) => m.ts === requestTs && m.content === "/compact";
    const dropRequestBubble = () => setMessages((m) => m.filter((x) => !isRequest(x)));

    // Where in the queue this request belongs, captured NOW rather than when the
    // round trip returns. Anything typed in between goes behind this index, so
    // the command keeps the position the operator gave it.
    const queuePos = queue.length;

    // Queue the live-session half UP FRONT when a turn is already running, so the
    // request holds the place the operator gave it instead of landing below
    // whatever the running turn streamed in the meantime.
    let preQueued = logBusyRef.current;
    if (preQueued) {
      queue.push("/compact");
      setQueuedStatus({ for: "/compact", status: BUSY_QUEUED });
    } else {
      setMessages((m) => [...m, { role: "user", content: "/compact", ts: requestTs }]);
    }

    try {
      const r = await chatApi.compactThread(id);
      // Optimistic: the fold itself is running detached now, so the divider is
      // drawn from what WILL fold. It is a label on old messages, not a state
      // anything depends on, and every path that loads a thread (open, switch,
      // focus resync) overwrites it with the server's number — so a fold that
      // fails corrects itself rather than needing to be waited for.
      setCompactedThrough(r.compacted_through + r.will_fold);
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
            // this bubble and the working indicator reads "Compacting".
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
        if (r.will_fold === 0) {
          // Nothing happened, so nothing should be left claiming it did.
          dropRequestBubble();
          setErr("Nothing to compact — the transcript is already short.");
        }
        // Otherwise the fold divider is the report, and it is already drawn.
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
