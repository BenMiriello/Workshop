import { useCallback, useState } from "react";
import { chatApi, newClientId, type MsgStatus } from "../../lib/chat";
import type { ThreadAction } from "./threadStore";
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
//
// THE REQUEST IS IDENTIFIED BY AN ID, not by its text and timestamp. This used
// to find its own bubble with `m.ts === requestTs && m.content === "/compact"`
// and its own queue entry by the string "/compact" — so two compactions in one
// session, or a "/compact" the operator typed themselves, were the same thing to
// every retract path here. The request is now one `client_id` from the moment it
// is made: the pending bubble carries it, the queue entry carries it, the turn
// is sent with it, and the committed event comes back carrying it, which is what
// makes the optimistic copy disappear exactly once.

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

// /clear — drop context, keep the transcript. See the file-level comment
// above for the full case for /compact's machinery; /clear needs almost none
// of it, and the reasoning is worth stating plainly so nobody "restores the
// symmetry" between the two commands later:
//
//   NO LIVE HALF. /compact's queue/ordering machinery exists to sequence a
//   TURN — the CLI's own /compact, which has to run in order relative to
//   whatever else the operator typed. /clear runs no turn: it resets the live
//   session by detaching it server-side (a fresh session mints on the NEXT
//   turn), it does not converse with the CLI about it. Nothing to queue.
//
//   NO MODEL CALL. The endpoint is a handful of field writes and returns in
//   milliseconds — there is no ~30s gap for a status line to cover.
//
//   NO REQUEST BUBBLE. A compact bubble earns its place because the fold it
//   describes is a real event with a real duration. A /clear that rendered a
//   "/clear" bubble would be announcing itself in the log AND in the divider
//   MessageList already draws from the boundary event — exactly the kind of
//   double announcement the rest of this file exists to avoid. The divider is
//   the report, and unlike a bubble it survives reload.
export type Compaction = {
  // Bumped after a fold so the context meter refetches: folding does not change
  // the message count, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
  // Set only while the request is sitting in the queue rather than in the log.
  // Keyed by the request's client id, not its text.
  queuedStatus: { for: string; status: MsgStatus } | null;
  clear: () => Promise<void>;
};

export function useCompaction({
  activeIdRef, runActiveRef, queue, dispatch,
  setCompacting, setErr, sendTurn, sync,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  // Is a run in flight on this thread? Server state, read from the store.
  runActiveRef: React.MutableRefObject<boolean>;
  queue: MessageQueue;
  dispatch: React.Dispatch<ThreadAction>;
  // Owned by the chat, not by this hook: the composer and the message queue both
  // have to hold while a fold is in flight, and both are declared before it.
  setCompacting: (v: boolean) => void;
  setErr: (m: string | null) => void;
  sendTurn: (text: string, clientId: string) => void;
  // Pull the log after a boundary lands, so the divider draws from the event
  // rather than from an optimistic count.
  sync: () => Promise<void>;
}): Compaction {
  const [ctxNonce, setCtxNonce] = useState(0);
  const [queuedStatus, setQueuedStatus] = useState<{ for: string; status: MsgStatus } | null>(null);

  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr("Nothing to compact. This chat has no messages yet."); return; }
    setErr(null);
    setCompacting(true);

    // The request shows in the log NOW, at the moment it was made — not when the
    // server answers. Its client id identifies it, so every no-op path below can
    // take this exact request back out again and no other.
    const requestId = newClientId();
    const dropRequestBubble = () => dispatch({ type: "sendRetracted", clientId: requestId });

    // Where in the queue this request belongs, captured NOW rather than when the
    // round trip returns. Anything typed in between goes behind this index, so
    // the command keeps the position the operator gave it.
    const queuePos = queue.length;

    // Queue the live-session half UP FRONT when a turn is already running, so the
    // request holds the place the operator gave it instead of landing below
    // whatever the running turn streamed in the meantime.
    let preQueued = runActiveRef.current;
    if (preQueued) {
      queue.push("/compact", requestId);
      setQueuedStatus({ for: requestId, status: BUSY_QUEUED });
    } else {
      dispatch({ type: "sendStarted", clientId: requestId, text: "/compact", ts: new Date().toISOString() });
    }

    try {
      const r = await chatApi.compactThread(id);
      // The divider is drawn from the `boundary` event the fold appended, so it
      // is read rather than guessed, and it survives a reload for free. The fold
      // itself runs detached; this sync picks up the boundary that is already
      // written, and the poll picks up anything that lands later.
      void sync();
      setCtxNonce((n) => n + 1);

      if (r.live_session) {
        if (!preQueued) {
          if (runActiveRef.current) {
            // A turn started while the fold was in flight. Hand it to the queue,
            // which renders its own pending bubble — so drop ours or the request
            // shows twice.
            dropRequestBubble();
            preQueued = true;
            queue.insertAt(queuePos, "/compact", requestId);   // invariant 5, not push
            setQueuedStatus({ for: requestId, status: BUSY_QUEUED });
          } else {
            // The live half runs as an ordinary turn: its reply streams in below
            // this bubble and the working indicator reads "Compacting". Sent
            // under the request's own id, so the pending bubble is reconciled
            // against the committed event instead of showing twice.
            sendTurn("/compact", requestId);
          }
        }
      } else {
        // No live session after all, so no turn is sent and nothing about this
        // request will ever be committed. Take the speculative entry back out —
        // wherever it went — rather than letting a literal "/compact" reach a
        // model as text, or leaving a bubble that says "sending" for a send that
        // is not going to happen.
        //
        // This is the one case whose DISPLAY changed with the store: the
        // optimistic bubble used to be an ordinary entry in the message list, so
        // it stayed on screen (until a reload silently dropped it) even though
        // the server had no record of it. An unsendable pending bubble cannot
        // linger now, and should not: the fold divider is the report, exactly as
        // it already is for /clear.
        if (preQueued) {
          queue.drop(requestId);
          setQueuedStatus(null);
        } else {
          dropRequestBubble();
        }
        // Nothing happened at all, so say so; otherwise the divider is the
        // report and it is already drawn.
        if (r.will_fold === 0) setErr("Nothing to compact. The transcript is already short.");
      }
    } catch (e) {
      if (preQueued) {
        queue.drop(requestId);
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
  }, [activeIdRef, runActiveRef, queue, dispatch, setCompacting, setErr, sendTurn, sync]);

  const clear = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr("Nothing to clear. This chat has no messages yet."); return; }
    setErr(null);
    // Reuses the compact hold: it closes the same window either command needs
    // closed — a message typed right after this call must not become a turn
    // before the boundary actually moves, or it would land above the cut and
    // read as having vanished.
    setCompacting(true);
    try {
      const r = await chatApi.clearThreadContext(id);
      if (!r.cleared) {
        setErr("Nothing to clear. This chat has no messages yet.");
        return;
      }
      // Nothing is written to the transcript: keeping it is the entire point of
      // /clear. No request bubble either — see the file-level /clear comment
      // above. The divider comes from the `boundary` event the clear appended.
      void sync();
      setCtxNonce((n) => n + 1);
    } catch (e) {
      setErr(`Couldn't clear: ${String(e)}`);
    } finally {
      setCompacting(false);
    }
  }, [activeIdRef, setCompacting, setErr, sync]);

  return { ctxNonce, compact, queuedStatus, clear };
}
