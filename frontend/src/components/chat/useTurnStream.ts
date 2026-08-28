import { useCallback, useEffect, useRef, useState } from "react";
import type { ThreadAction, ThreadState } from "./threadStore";
import type { ChatProblem } from "./chatProblem";
import { CONSUMED_WIRE_KINDS, type ConsumedWireKind } from "./streamWire";

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
// with `turnDelta`, and "is a run in flight" is `state.activeTurn`, which is
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
  /** This tab has GIVEN UP watching the current run: the reconnect budget was
   *  spent, or Stop closed the socket.
   *
   *  THERE IS NO `attached` FIELD, deliberately. One was maintained here through
   *  four setters and returned for months, and no consumer ever read it: Chat
   *  destructures this hook twice and never takes it. All it did was re-render
   *  the whole chat on every attach and every detach. Deleted 2026-08-26 with
   *  the turn-lifecycle fold; do not reintroduce it as the answer to "is a turn
   *  running" — that is the store's `activeTurn`.
   *
   *  `detached` used to be read as `!attached`, and that conflated "we stopped
   *  watching" with "we have not started watching YET" — which is the state
   *  every turn passes through between the server reporting it active and this
   *  tab's attach effect running one tick later. The indicator therefore
   *  flashed "Still running" (the wording for a socket that ENDED) at the top
   *  of a turn the operator had just started themselves, before flipping to
   *  "Working". Measured window between `POST /turns` answering and
   *  `GET /turns/{id}/stream` opening: 60-140ms. */
  detached: boolean;
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
  isDetached: (turnId: string) => boolean;
};

export function useTurnStream({
  dispatch,
  stateRef,
  sync,
  onTurnEnd,
  onError,
}: {
  dispatch: React.Dispatch<ThreadAction>;
  stateRef: React.MutableRefObject<ThreadState>;
  /** Pull the log up to date. Awaited before a turn is retired. */
  sync: () => Promise<void>;
  onTurnEnd: () => void;
  onError: (problem: ChatProblem | null) => void;
}): TurnStream {
  // Only the give-up paths set this. Attaching clears it.
  const [detached, setDetached] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  const sseRef = useRef<EventSource | null>(null);
  const finishingRef = useRef(false);
  const reconnectsRef = useRef(0);
  const detachedRef = useRef<Set<string>>(new Set());
  const attachRef = useRef<(turnId: string) => void>(() => {});
  // The pending reconnect, held so teardown can cancel it. Without this handle
  // the timer fired after the hook was torn down and opened a socket nothing
  // owned: `closeStream` and the unmount cleanup both run BEFORE the delay
  // elapses and neither set `finishingRef`, so the callback's guard passed and
  // it attached into a dead store. The server heartbeat then kept that orphan
  // open for the life of the page, once per dropped stream, which in a PWA that
  // is never closed is a socket and a parse-per-second accumulating for days.
  const reconnectTimerRef = useRef<number | undefined>(undefined);

  // Callers pass inline lambdas; refs keep `attach` referentially stable.
  const cb = useRef({ dispatch, sync, onTurnEnd, onError });
  cb.current = { dispatch, sync, onTurnEnd, onError };

  const closeStream = useCallback(() => {
    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = undefined;
    sseRef.current?.close();
    sseRef.current = null;
    setDetached(false);
    setReconnecting(false);
  }, []);

  const hasOpenStream = useCallback(() => sseRef.current !== null, []);
  const isDetached = useCallback((turnId: string) => detachedRef.current.has(turnId), []);

  const attach = useCallback((turnId: string) => {
    // A reconnect that is already scheduled would open a SECOND socket behind
    // this one, so it is cancelled here as well as on teardown.
    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = undefined;
    sseRef.current?.close();
    finishingRef.current = false;
    detachedRef.current.delete(turnId);
    setDetached(false);

    // REWIND BEFORE OPENING THE SOCKET. A fresh EventSource carries no
    // Last-Event-ID (only the browser's own automatic retry does, and this is
    // not that), so the server replays the turn from offset 0 every time. The
    // reducer APPENDS, and `handleDrop` has usually just run a sync that
    // restored the server's snapshot, so folding a replay into what is already
    // there rendered the entire turn a second time. Rewinding costs nothing:
    // the replay is the same events that built the view being dropped.
    cb.current.dispatch({ type: "turnReattached", turnId });

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

    // ONE TABLE, KEYED BY THE WIRE VOCABULARY. `Record<ConsumedWireKind, ...>`
    // is exhaustive, so a kind declared consumed in streamWire.ts and not
    // handled here does not compile, and the listeners are registered by
    // iterating this table below, so a handler cannot be written and left
    // unsubscribed. Both halves of how `cut` went missing for a day and a half.
    const handlers: Record<ConsumedWireKind, (e: Event) => void> = {
      text: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: { kind: "text", content: String(p.content || ""), parent: p.parent as string | null },
        });
      },
      // The model's reasoning, folded into its own thinking segment. Same shape
      // as text on the wire; the reducer keeps it a separate kind so it never
      // joins the answer.
      thinking: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: { kind: "thinking", content: String(p.content || ""), parent: p.parent as string | null },
        });
      },
      tool: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: {
            kind: "tool",
            name: p.name as string, input: p.input as string, detail: p.detail as string | null,
            id: p.id as string, agent: p.agent as string | null, parent: p.parent as string | null,
          },
        });
      },
      // Only a subagent's own result is rendered (it closes that subagent's
      // segment); every other tool result is plumbing the operator already sees
      // the effect of, and the reducer drops it.
      tool_result: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: {
            kind: "tool_result", tool_use_id: p.tool_use_id as string, text: p.text as string,
            // The server's own reading of whether this result is an outcome or
            // the stop cutting the call off. See segments.closeStep.
            interrupted: Boolean(p.interrupted),
          // WITHOUT THIS EVERY LIVE STEP READS AS SUCCEEDED. The server sends
          // `is_error` and this listener used to drop it, so `ok: !is_error`
          // was unconditionally true while the turn ran: a failed step merged
          // into a collapsed group, never auto-opened, and carried no failure
          // tag, then flipped to red the moment the turn committed and the
          // server's own segments replaced these. The outcome only ever
            // existed after the fact, which is the opposite of when it matters.
            is_error: Boolean(p.is_error),
            parent: (p.parent as string) || null,
          },
        });
      },
      // The turn is blocked, asking. This is the one event the operator MUST
      // see: nothing else will unblock the run.
      permission: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: {
            kind: "permission",
            id: p.id as string, tool: p.tool as string, label: p.label as string,
            detail: p.detail as string,
            input: (p.input || {}) as Record<string, unknown>,
            suggestions: (p.suggestions || []) as { type?: string; mode?: string }[],
          },
        });
      },
      permission_resolved: (e) => {
        markAlive();
        const p = parse(e);
        cb.current.dispatch({
          type: "turnDelta", turnId,
          event: {
            kind: "permission_resolved",
            id: p.id as string, state: p.state as string, stale: Boolean(p.stale),
          },
        });
      },
      // WHERE A MESSAGE SENT MID-TURN LANDED, and the reason this table exists.
      //
      // The reducer has handled this since the cut design shipped and nothing
      // ever subscribed to it, so on the streaming tab — the one the operator
      // watches a turn run in — the live reply carried no recorded cut position
      // at all and fell back to the unit index decision 0018 replaced. That
      // index is resolved against a segment list that keeps growing, so a
      // message drifted upward as the turn worked and ended up ABOVE work that
      // had already happened when it was sent. Reported 2026-08-14, and
      // 2026-08-12 before that against the committed half, which is why it read
      // as a fix that did not hold: two halves, one of them never connected.
      cut: (e) => {
        markAlive();
        const p = parse(e);
        const ref = String(p.ref || "");
        if (ref) cb.current.dispatch({ type: "turnDelta", turnId, event: { kind: "cut", ref } });
      },
      // The model's own report of how full its window is, sent whenever the
      // number changes. This is the ONLY live source: the thread's stored
      // reading is written at finalize, so without this event the meter cannot
      // move during a turn at all.
      context: (e) => {
        markAlive();
        const p = parse(e);
        const tokens = Number(p.tokens);
        if (Number.isFinite(tokens) && tokens > 0) {
          cb.current.dispatch({ type: "turnContext", turnId, tokens });
        }
      },
      ping: markAlive, // server heartbeat (~1s) keeps this warm
      error: (e) => {
        // An in-band error EVENT (carries data) — a turn-level error from the
        // server, NOT a connection drop (that is es.onerror, which has no data).
        try {
          const d = JSON.parse((e as MessageEvent).data || "{}");
          if (d.payload?.detail) {
            // NO ENDPOINT. The failure is in the TURN, not in the URL the turn
            // happened to arrive over, so naming the stream path tells the
            // operator nothing they can act on. Same rule the design system
            // states for `where`: name the endpoint only where naming it helps
            // someone act. See the dropped-stream branch below.
            cb.current.onError({ text: String(d.payload.detail) });
          }
        } catch { /* ignore */ }
      },
      write_blocked: (e) => {
        // The tree is mounted read-only because this session does not hold its
        // lease, so a write came back EROFS. Reported, not swallowed: a write
        // failing where only the model can see it is the whole reason the
        // read-only mount needed an observable event in the first place.
        try {
          const d = JSON.parse((e as MessageEvent).data || "{}");
          const offer = d.payload || {};
          const detail = offer.default?.detail;
          if (!detail) return;
          const path = offer.blocked_path;
          // The path that matters here is the BLOCKED one, and it is already in
          // the sentence. The stream endpoint is not where the write failed and
          // is not something the operator can act on.
          cb.current.onError({
            text: path ? `${detail} The write to ${path} did not happen.`
                       : detail,
          });
        } catch { /* ignore */ }
      },
      // Defined below, once `finish` exists.
      done: () => { void finish(); },
      end: () => { void finish(); },
    };

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
      // The sync brings the committed reply AND clears `active_turn` in a single
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
        if (stateRef.current.activeTurn?.turnId !== turnId) break;
        await new Promise((r) => window.setTimeout(r, COMMIT_SETTLE_MS));
      }
      // A floor, not the expected path: if the commit never showed up, stop
      // claiming a run is in flight and let the poll correct the transcript.
      cb.current.dispatch({ type: "turnCommitted", turnId });
      setDetached(false);
      setReconnecting(false);
      cb.current.onTurnEnd();
      // NOTHING IS DRAINED FROM HERE ANY MORE. A turn ending used to be this
      // tab's cue to send whatever the operator had typed ahead, which made
      // delivery of an already-sent message depend on a socket being open in a
      // browser that was still looking at this thread. The server drains its
      // own queue when the turn ends (services/chat_drain); the sync above is
      // how this tab finds out.
    };
    // REGISTERED BY ITERATING THE TABLE, so a handler cannot be written and
    // left unsubscribed. Ignored kinds (started, result, checkpoint, meta,
    // user_echo) are declared in streamWire.ts with the reason; they arrive and
    // nothing listens, deliberately.
    for (const kind of CONSUMED_WIRE_KINDS) es.addEventListener(kind, handlers[kind]);

    const handleDrop = async () => {
      if (finishingRef.current) return;
      es.close();
      if (sseRef.current === es) sseRef.current = null;
      // Did the turn really end, or did the socket? Only the server knows.
      await cb.current.sync();
      if (finishingRef.current) return;
      if (stateRef.current.activeTurn?.turnId !== turnId) { void finish(); return; }
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
        setDetached(true);
        setReconnecting(false);
        // Hand the segments back to the poll: with no socket held, the stream is
        // no longer replaying, so the poll snapshot must resume advancing them
        // (see threadStore `streamOwned`). A re-attach later takes ownership back.
        cb.current.dispatch({ type: "turnStreamDropped", turnId });
        // NOTHING IS SAID ON SCREEN, BECAUSE THE SCREEN ALREADY SAYS IT.
        //
        // This used to raise a problem block reading "Lost the connection to
        // this turn. It is still running; reattaching." carrying the endpoint
        // `/api/chat/turns/<id>/stream`. Three things wrong with it:
        //
        //  1. It printed an internal API path to someone who cannot act on one.
        //     Same defect as the drift banner deleted 2026-08-28: app copy
        //     addressed to a developer.
        //  2. It said what the indicator beside it was already saying. Setting
        //     `detached` is exactly what makes turnLifecycle report "Still
        //     running" (turnLifecycle.ts, `watching === "detached"`), so the
        //     block and the indicator stated the same fact one line apart, and
        //     the screen read as contradicting itself.
        //  3. "reattaching" describes the poll's job, not this function's. The
        //     budget is spent deliberately here; the liveness poll in Chat is
        //     what attaches again, on its own pace.
        //
        // The run is still in the store, the poll keeps its snapshot current,
        // and the indicator keeps saying the turn is running. That is the whole
        // truth the operator needs, and it is already on screen. The path goes
        // to the console, where a developer can reach it.
        console.warn(
          `chat: stream dropped after ${MAX_RECONNECTS} reconnects; the poll takes over`,
          { turnId, endpoint: `/api/chat/turns/${turnId}/stream` },
        );
        return;
      }
      reconnectsRef.current += 1;
      setReconnecting(true);
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = undefined;
        if (!finishingRef.current) attachRef.current(turnId);
      }, RECONNECT_DELAY_MS);
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) void handleDrop();
    };
  }, [stateRef]);
  attachRef.current = attach;

  const beginStop = useCallback(() => {
    const turnId = stateRef.current.activeTurn?.turnId;
    finishingRef.current = true;
    if (turnId) detachedRef.current.add(turnId);
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

  useEffect(() => () => {
    window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = undefined;
    sseRef.current?.close();
    sseRef.current = null;
  }, []);

  return { detached, reconnecting, attach, beginStop, stopWatching,
           closeStream, hasOpenStream, isDetached };
}
