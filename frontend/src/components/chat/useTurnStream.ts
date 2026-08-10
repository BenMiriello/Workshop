import { useCallback, useEffect, useRef, useState } from "react";
import type { ThreadAction, ThreadState } from "./threadStore";

// The live turn's SSE channel, and nothing else.
//
// EVERY chat (general, app, project, build) streams through
// POST /threads/{id}/turns + GET /turns/{id}/stream, so every turn shows its
// work live instead of a blocking spinner. The backend picks the tool surface
// and session from the provider class; the client is transport-uniform.
//
// WHAT THIS HOOK NO LONGER OWNS. It used to hold the turn's segments, the
// server's copy of the transcript, and the rule for when a turn stops owning the
// tail of the log. All three now live in the store: the socket folds events in
// with `runDelta`, and "is a run in flight" is `state.activeRun`, which is
// SERVER state. That deletes `pendingSegments` (a client-only cache of a turn's
// output, with a written invariant about when it could be cleared), the
// `logBusy`/`streaming` split (two questions whose answers diverged for one
// round trip on every turn), and the backward scan for a streaming placeholder.
//
// THE HARD-WON RULES THAT REMAIN, each from a real incident:
//
//  - A dropped connection is NOT a finished turn. Proxy idle-timeouts and blips
//    are normal on long turns. Ask the server whether the run is still active
//    and re-attach if it is; the stream replays from the start via Last-Event-ID.
//  - Only a CLOSED socket is terminal. CONNECTING means the browser is already
//    auto-retrying — let it.
//  - Any inbound frame means healthy: reset the reconnect budget, so one long
//    turn with the odd drop never exhausts the cap.
//  - Running out of budget must not finish the turn. It stops the socket, not
//    the run. The transcript keeps showing the run (the store still has it, and
//    the poll keeps its snapshot current) and the indicator says so.
//  - Read before retiring. Sync the log FIRST, then flip out of streaming, so
//    there is no window in which the chat looks idle but the reply is not in
//    the transcript yet.

const MAX_RECONNECTS = 8;
const RECONNECT_DELAY_MS = 1200;
// The gap between the `done` frame and the committed reply is a single append,
// so this settles on the first or second try in practice.
const COMMIT_SETTLE_TRIES = 10;
const COMMIT_SETTLE_MS = 300;

export type TurnStream = {
  /** A socket for the current run is open (or being retried) here. Chooses the
   *  indicator's wording; it is NOT the question "is a turn running", which is
   *  the store's `activeRun`. */
  attached: boolean;
  reconnecting: boolean;
  attach: (turnId: string) => void;
  /** Claim the end of this run WITHOUT closing the socket, before asking the
   *  server to stop it. The server pushes `done` at the top of its commit, so
   *  that frame lands while the stop request is still in flight — and its
   *  handler retires the turn and DRAINS THE QUEUE, which is the one thing a
   *  stop must not do. Claiming first suppresses it; the socket stays open so
   *  the last of the output still renders. */
  beginStop: () => void;
  /** Close this tab's socket for the current run and do not silently re-attach.
   *  The SOCKET half of Stop: the run itself is ended by the server
   *  (`api.stopTurn`), and the caller does that first. On its own this leaves a
   *  run running, which is what the old Stop button did and why it never
   *  stopped anything. */
  stopWatching: () => void;
  closeStream: () => void;
  hasOpenStream: () => boolean;
  /** Runs this tab has deliberately stopped watching, so the re-attach effect
   *  leaves them alone. */
  isDetached: (runId: string) => boolean;
};

export function useTurnStream({
  dispatch,
  stateRef,
  sync,
  onTurnEnd,
  onError,
  onDrainQueue,
}: {
  dispatch: React.Dispatch<ThreadAction>;
  stateRef: React.MutableRefObject<ThreadState>;
  /** Pull the log up to date. Awaited before a turn is retired. */
  sync: () => Promise<void>;
  onTurnEnd: () => void;
  onError: (msg: string | null) => void;
  onDrainQueue: () => void;
}): TurnStream {
  const [attached, setAttached] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  const sseRef = useRef<EventSource | null>(null);
  const finishingRef = useRef(false);
  const reconnectsRef = useRef(0);
  const detachedRef = useRef<Set<string>>(new Set());
  const attachRef = useRef<(turnId: string) => void>(() => {});

  // Callers pass inline lambdas; refs keep `attach` referentially stable.
  const cb = useRef({ dispatch, sync, onTurnEnd, onError, onDrainQueue });
  cb.current = { dispatch, sync, onTurnEnd, onError, onDrainQueue };

  const closeStream = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    setAttached(false);
    setReconnecting(false);
  }, []);

  const hasOpenStream = useCallback(() => sseRef.current !== null, []);
  const isDetached = useCallback((runId: string) => detachedRef.current.has(runId), []);

  const attach = useCallback((turnId: string) => {
    sseRef.current?.close();
    finishingRef.current = false;
    detachedRef.current.delete(turnId);
    setAttached(true);

    // REWIND BEFORE OPENING THE SOCKET. A fresh EventSource carries no
    // Last-Event-ID (only the browser's own automatic retry does, and this is
    // not that), so the server replays the turn from offset 0 every time. The
    // reducer APPENDS, and `handleDrop` has usually just run a sync that
    // restored the server's snapshot, so folding a replay into what is already
    // there rendered the entire turn a second time. Rewinding costs nothing:
    // the replay is the same events that built the view being dropped.
    cb.current.dispatch({ type: "runReattached", runId: turnId });

    const es = new EventSource(`/api/chat/turns/${turnId}/stream`);
    sseRef.current = es;

    const markAlive = () => {
      reconnectsRef.current = 0;
      setReconnecting(false);
    };

    // One reducer for every segment-shaped event, mirroring the server's
    // _segment_ingest so the live turn and the reloaded one agree — in
    // particular about which steps belong to a subagent rather than to the turn.
    const parse = (e: Event): Record<string, unknown> => {
      try {
        return (JSON.parse((e as MessageEvent).data).payload || {}) as Record<string, unknown>;
      } catch {
        return {};
      }
    };

    es.addEventListener("text", (e) => {
      markAlive();
      const p = parse(e);
      cb.current.dispatch({
        type: "runDelta", runId: turnId,
        event: { kind: "text", content: String(p.content || ""), parent: p.parent as string | null },
      });
    });
    es.addEventListener("tool", (e) => {
      markAlive();
      const p = parse(e);
      cb.current.dispatch({
        type: "runDelta", runId: turnId,
        event: {
          kind: "tool",
          name: p.name as string, input: p.input as string, detail: p.detail as string | null,
          id: p.id as string, agent: p.agent as string | null, parent: p.parent as string | null,
        },
      });
    });
    // Only a subagent's own result is rendered (it closes that subagent's
    // segment); every other tool result is plumbing the operator already sees
    // the effect of, and the reducer drops it.
    es.addEventListener("tool_result", (e) => {
      markAlive();
      const p = parse(e);
      cb.current.dispatch({
        type: "runDelta", runId: turnId,
        event: { kind: "tool_result", tool_use_id: p.tool_use_id as string, text: p.text as string },
      });
    });
    // The turn is blocked, asking. This is the one event the operator MUST see:
    // nothing else will unblock the run.
    es.addEventListener("permission", (e) => {
      markAlive();
      const p = parse(e);
      cb.current.dispatch({
        type: "runDelta", runId: turnId,
        event: {
          kind: "permission",
          id: p.id as string, tool: p.tool as string, label: p.label as string,
          detail: p.detail as string,
          input: (p.input || {}) as Record<string, unknown>,
          suggestions: (p.suggestions || []) as { type?: string; mode?: string }[],
        },
      });
    });
    es.addEventListener("permission_resolved", (e) => {
      markAlive();
      const p = parse(e);
      cb.current.dispatch({
        type: "runDelta", runId: turnId,
        event: {
          kind: "permission_resolved",
          id: p.id as string, state: p.state as string, stale: Boolean(p.stale),
        },
      });
    });
    es.addEventListener("ping", markAlive); // server heartbeat (~1s) keeps this warm
    es.addEventListener("error", (e) => {
      // An in-band error EVENT (carries data) — a turn-level error from the
      // server, NOT a connection drop (that is es.onerror, which has no data).
      try {
        const d = JSON.parse((e as MessageEvent).data || "{}");
        if (d.payload?.detail) cb.current.onError(String(d.payload.detail));
      } catch { /* ignore */ }
    });

    const finish = async () => {
      if (finishingRef.current) return;
      finishingRef.current = true;
      es.close();
      sseRef.current = null;
      reconnectsRef.current = 0;

      // READ FIRST, THEN RETIRE, IN ONE COMMIT.
      //
      // This used to flip out of streaming, then fetch, then clear the segments
      // — so for a whole round trip the turn was "not streaming" while still
      // rendered as the live band below the messages, and anything sent in that
      // window appended above a turn that had already finished.
      //
      // The sync brings the committed reply AND clears `active_run` in a single
      // dispatch, so the live block becomes its persisted message in one React
      // commit with no gap to fall into.
      //
      // AND `done` DOES NOT MEAN COMMITTED. The server pushes the `done` frame
      // at the top of _commit_turn and appends the reply after it, so a sync
      // fired the instant this frame arrives can legitimately find the run still
      // active and no message yet. Retiring on that answer would blank the tail
      // of the transcript until the next poll. Wait for the server to say the
      // run is over instead, and keep the live block (and its indicator) up
      // meanwhile — it is still true.
      for (let i = 0; i < COMMIT_SETTLE_TRIES; i++) {
        await cb.current.sync();
        if (stateRef.current.activeRun?.runId !== turnId) break;
        await new Promise((r) => window.setTimeout(r, COMMIT_SETTLE_MS));
      }
      // A floor, not the expected path: if the commit never showed up, stop
      // claiming a run is in flight and let the poll correct the transcript.
      cb.current.dispatch({ type: "runCommitted", runId: turnId });
      setAttached(false);
      setReconnecting(false);
      cb.current.onTurnEnd();
      cb.current.onDrainQueue();
    };
    es.addEventListener("done", finish);
    es.addEventListener("end", finish);

    const handleDrop = async () => {
      if (finishingRef.current) return;
      es.close();
      if (sseRef.current === es) sseRef.current = null;
      // Did the turn really end, or did the socket? Only the server knows.
      await cb.current.sync();
      if (finishingRef.current) return;
      if (stateRef.current.activeRun?.runId !== turnId) { void finish(); return; }
      if (reconnectsRef.current >= MAX_RECONNECTS) {
        // OUT OF BUDGET IS NOT THE END OF THE TURN. Stop the fast retry loop and
        // stop claiming a socket, but leave the run in the store: it is still in
        // flight, the poll keeps its snapshot current, and the indicator reads
        // "Still running" rather than the transcript going quiet.
        //
        // The slow path takes over from here: the liveness poll in Chat sees the
        // server still reports this run active with no socket held, and attaches
        // again — which resets `finishingRef` and, on the first frame that
        // arrives, the reconnect budget too. The budget is deliberately NOT
        // reset here; leaving it spent is what keeps a genuinely dead stream on
        // the poll's pace instead of back in a tight retry loop.
        finishingRef.current = true;
        setAttached(false);
        setReconnecting(false);
        cb.current.onError("Lost the connection to this turn. It is still running; reattaching.");
        return;
      }
      reconnectsRef.current += 1;
      setReconnecting(true);
      window.setTimeout(() => {
        if (!finishingRef.current) attachRef.current(turnId);
      }, RECONNECT_DELAY_MS);
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) void handleDrop();
    };
  }, [stateRef]);
  attachRef.current = attach;

  const beginStop = useCallback(() => {
    const runId = stateRef.current.activeRun?.runId;
    finishingRef.current = true;
    if (runId) detachedRef.current.add(runId);
  }, [stateRef]);

  const stopWatching = useCallback(() => {
    // Claim the finish so a pending drop handler cannot re-attach behind us.
    // Idempotent with beginStop, which the stop path has usually run already.
    beginStop();
    closeStream();
    // Pull the log: by now the server has committed whatever the run produced
    // before it was stopped, and this is what puts that reply on screen.
    // The run ended, so everything a normal ending refreshes still has to
    // happen: the thread list, the auto-name, the task panel. `finish` is the
    // one that usually does it and it is suppressed here by design.
    void cb.current.sync().then(() => cb.current.onTurnEnd());
    // THE QUEUE IS LEFT ALONE, deliberately. Stop is how the operator takes the
    // wheel back; sending everything they had typed ahead, immediately and
    // unattended, is the opposite of what they just asked for. The queued
    // messages stay queued until they send them.
  }, [beginStop, closeStream]);

  useEffect(() => () => { sseRef.current?.close(); sseRef.current = null; }, []);

  return { attached, reconnecting, attach, beginStop, stopWatching, closeStream,
           hasOpenStream, isDetached };
}
