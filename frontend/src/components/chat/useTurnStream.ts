import { useCallback, useEffect, useRef, useState } from "react";
import { chatApi, type ChatMsg, type ChatSegment } from "../../lib/chat";
import { applyStreamEvent, type StreamEvent } from "./segments";

// The live turn: its SSE channel, its resilience, and what "still running" means.
//
// EVERY chat (general, app, project, build) streams through
// POST /threads/{id}/turns + GET /turns/{id}/stream, so every turn shows its work
// (tool calls, interim text) live instead of a blocking spinner. The backend
// picks the tool surface and session from the provider class; the client is
// transport-uniform.
//
// THE HARD-WON RULES:
//
//  - `pendingSegments` is a render-ahead CACHE of server state, never the only
//    home of a turn's output. Nothing may clear it until the server's copy of
//    that turn is in `messages`, or the record of what the turn did disappears
//    from the log.
//  - A dropped connection is NOT a finished turn. Proxy idle-timeouts and
//    network blips are normal on long turns. Finalizing blindly is what made
//    turns look like they "just stopped": check whether the turn actually
//    completed server-side and re-attach if it did not. The server replays the
//    turn's events from the start via Last-Event-ID.
//  - Running out of reconnect budget on a turn the server says is STILL RUNNING
//    must not call finish(). That would drop to an idle-looking UI and dispatch
//    queued messages into a second concurrent turn. Stop retrying, keep the work
//    on screen, say so plainly, and let the liveness poll re-attach.
//  - Only a CLOSED socket is terminal. CONNECTING means the browser is already
//    auto-retrying with Last-Event-ID — let it.
//  - Any inbound frame means healthy: reset the reconnect budget, so one long
//    turn with the odd drop never exhausts the cap.
//  - LIVENESS IS SERVER STATE. The local `streaming` flag only knows about turns
//    THIS tab attached to, so a turn running in another tab, on another device,
//    or one whose socket died past its budget rendered as a completely idle chat
//    while work was in flight. Poll while idle and re-attach the moment the
//    server says a turn is live.
//  - Stopping reconciles BEFORE handing off. Stop used to drop straight into the
//    queue dispatch, and the next turn's attach then cleared pendingSegments, so
//    everything the stopped turn had already done vanished from the transcript.

const MAX_RECONNECTS = 8;
const RECONNECT_DELAY_MS = 1200;
const LIVENESS_POLL_MS = 10_000;

// The thread's live turn, if any — the assistant message carrying
// `meta.streaming`. NOT necessarily the LAST message: a message sent mid-turn
// files AFTER it now (append-only, in send order), so this scans backward for
// it instead of checking `messages[messages.length - 1]`. Mirrors the
// server's `_live_turn_id` (routes/chat.py), which got this same fix first —
// every caller here that still checked the last message only stopped
// reattaching, or worse, concluded a running turn had ended, the moment an
// interjection landed after the placeholder.
export function findLiveTurn(msgs: ChatMsg[]): ChatMsg | null {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role === "assistant" && m.meta?.streaming && m.meta?.turn_id) return m;
  }
  return null;
}

export type TurnStream = {
  streaming: boolean;
  streamingRef: React.MutableRefObject<boolean>;
  // "A turn still owns the TAIL of the log." True while streaming AND through
  // the settle that follows, until the turn's output has become a persisted
  // message. THIS, not `streaming`, is the question every send has to ask.
  //
  // The two are not the same and the gap between them is a bug factory. A turn
  // stops streaming before its reply reaches `messages` — on finish there is a
  // getThread round trip, and Stop resolves the same way. In that gap a send
  // that consulted `streaming` took the not-streaming path, appended straight to
  // `messages`, and rendered ABOVE the finished turn still sitting in the live
  // band. One flag, one answer, every exit path.
  logBusyRef: React.MutableRefObject<boolean>;
  reconnecting: boolean;
  // Seconds elapsed on the live turn, for the working indicator. Anchored to the
  // turn's real start so a reload mid-turn shows true elapsed time rather than
  // restarting from zero, and it keeps running across reconnects.
  elapsed: number;
  pendingSegments: ChatSegment[];
  setPendingSegments: React.Dispatch<React.SetStateAction<ChatSegment[]>>;
  // The turn currently running, if any. A message sent while it runs is handed
  // straight to it rather than waiting for the whole turn to end.
  liveTurnRef: React.MutableRefObject<string | null>;
  attach: (turnId: string, threadId: string, isFirst: boolean) => void;
  // A turn is being started by this client: show it as running from the moment
  // the operator hits send, not from when the POST resolves.
  markStarting: () => void;
  // The POST never succeeded, so no stream will ever attach.
  markFailed: () => void;
  // How many reply segments have streamed. Nothing positions messages by it any
  // more (they append in send order); kept as a plain progress reading, and
  // the log can split the live turn at the point the message landed.
  segmentCount: () => number;
  // Anchor the elapsed clock to a known start (a reload reattaching mid-turn).
  anchorStart: (startedMs: number) => void;
  stop: () => void;
  closeStream: () => void;
  // Whether a stream socket is currently open. The resync and liveness paths
  // must not act while the stream owns the log.
  hasOpenStream: () => boolean;
};

export function useTurnStream({
  onMessages,
  onTurnEnd,
  onError,
  onNotes,
  activeIdRef,
  dispatchQueue,
  appendSegmentsAsMessage,
}: {
  // The server's copy of the transcript, after a turn ends or is stopped.
  onMessages: (msgs: ChatMsg[]) => void;
  // A turn finished: refresh derived surfaces (thread list, task panel).
  onTurnEnd: (wasFirstExchange: boolean) => void;
  onError: (msg: string | null) => void;
  // The finished turn applied a provider action (e.g. an app persona editing a
  // budget plan); it rides in the final message's meta.notes.
  onNotes: () => void;
  activeIdRef: React.MutableRefObject<string | null>;
  dispatchQueue: () => void;
  // Stop could not reach the server: fold what is on screen into the log rather
  // than losing it.
  appendSegmentsAsMessage: (segs: ChatSegment[]) => void;
}): TurnStream {
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef(false);
  streamingRef.current = streaming;
  const [reconnecting, setReconnecting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [pendingSegments, setPendingSegments] = useState<ChatSegment[]>([]);
  // Kept in sync on every render, exactly like streamingRef. The live band is
  // rendered on the same condition (see ChatLog), so this is literally "is a
  // turn occupying the end of the transcript right now".
  const logBusyRef = useRef(false);
  logBusyRef.current = streaming || pendingSegments.length > 0;
  const pendingRef = useRef<ChatSegment[]>(pendingSegments);
  pendingRef.current = pendingSegments;

  const sseRef = useRef<EventSource | null>(null);
  const finishingRef = useRef(false);
  const liveTurnRef = useRef<string | null>(null);
  const reconnectsRef = useRef(0);
  const turnStartRef = useRef(0);
  // Self-reference, so a drop handler can re-attach without a render cycle.
  const attachRef = useRef<(turnId: string, threadId: string, isFirst: boolean) => void>(() => {});

  // Callers pass inline lambdas; refs keep `attach` referentially stable.
  const cb = useRef({ onMessages, onTurnEnd, onError, onNotes, dispatchQueue, appendSegmentsAsMessage });
  cb.current = { onMessages, onTurnEnd, onError, onNotes, dispatchQueue, appendSegmentsAsMessage };

  const anchorStart = useCallback((startedMs: number) => {
    turnStartRef.current = Number.isFinite(startedMs) ? startedMs : Date.now();
  }, []);

  const closeStream = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
  }, []);

  const hasOpenStream = useCallback(() => sseRef.current !== null, []);
  const segmentCount = useCallback(() => pendingRef.current.length, []);

  const markStarting = useCallback(() => {
    setStreaming(true);
    setPendingSegments([]);
    turnStartRef.current = Date.now();   // fresh turn: anchor the clock now
  }, []);

  const markFailed = useCallback(() => setStreaming(false), []);

  const attach = useCallback((turnId: string, threadId: string, isFirst: boolean) => {
    sseRef.current?.close();
    finishingRef.current = false;
    liveTurnRef.current = turnId;
    setStreaming(true);
    setPendingSegments([]);

    // One reducer for every segment-shaped event, mirroring the server's
    // _segment_ingest so the live turn and the reloaded one agree — in
    // particular about which steps belong to a subagent rather than to the turn.
    const fold = (ev: StreamEvent) => setPendingSegments((segs) => applyStreamEvent(segs, ev));

    const es = new EventSource(`/api/chat/turns/${turnId}/stream`);
    sseRef.current = es;

    const markAlive = () => {
      reconnectsRef.current = 0;
      setReconnecting(false);
    };

    es.addEventListener("text", (e) => {
      markAlive();
      try {
        const p = JSON.parse((e as MessageEvent).data).payload || {};
        fold({ kind: "text", content: p.content || "", parent: p.parent });
      } catch { /* ignore */ }
    });
    es.addEventListener("tool", (e) => {
      markAlive();
      try {
        const p = JSON.parse((e as MessageEvent).data).payload || {};
        fold({ kind: "tool", name: p.name, input: p.input, id: p.id, agent: p.agent, parent: p.parent });
      } catch { /* ignore */ }
    });
    // Only a subagent's own result is rendered (it closes that subagent's
    // segment); every other tool result is plumbing and the reducer drops it.
    es.addEventListener("tool_result", (e) => {
      markAlive();
      try {
        const p = JSON.parse((e as MessageEvent).data).payload || {};
        fold({ kind: "tool_result", tool_use_id: p.tool_use_id, text: p.text });
      } catch { /* ignore */ }
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
      liveTurnRef.current = null;
      es.close();
      sseRef.current = null;
      reconnectsRef.current = 0;

      // READ FIRST, THEN RETIRE THE TURN IN ONE COMMIT.
      //
      // This used to flip `streaming` off, then await getThread, then clear the
      // segments — so for a whole network round trip the turn was "not
      // streaming" while still rendered as the live band BELOW `messages`.
      // Anything the operator sent in that window took the not-streaming path,
      // appended to `messages`, and landed ABOVE a turn that had already
      // finished. That is the out-of-order message.
      //
      // Fetching first collapses the window. While the read is in flight the
      // turn is still live in every sense the rest of the app can observe, so a
      // message sent then QUEUES (below the live turn, which is the truthful
      // place for something not yet sent) instead of jumping above it. There is
      // exactly one moment where the live turn becomes its persisted message,
      // and React batches these into a single commit.
      let server: ChatMsg[] | null = null;
      try {
        const t = await chatApi.getThread(threadId);
        server = t.messages;
      } catch { /* keep what is on screen; the liveness poll resyncs */ }

      setStreaming(false);
      setReconnecting(false);
      turnStartRef.current = 0;
      if (server) {
        cb.current.onMessages(server);
        const last = server[server.length - 1];
        if (last?.meta?.notes?.length) cb.current.onNotes();
      }
      // Safe now, and only now: the server's copy of this turn is in `messages`,
      // which is the invariant stated at the top of this file.
      setPendingSegments([]);
      cb.current.onTurnEnd(isFirst);
      cb.current.dispatchQueue();
    };
    es.addEventListener("done", finish);
    es.addEventListener("end", finish);

    const handleDrop = async () => {
      if (finishingRef.current) return;
      es.close();
      if (sseRef.current === es) sseRef.current = null;
      try {
        const t = await chatApi.getThread(threadId);
        const stillRunning = !!findLiveTurn(t.messages);
        if (!stillRunning) { void finish(); return; } // turn really ended — sync + stop
      } catch { /* network error — assume still running and retry */ }
      if (reconnectsRef.current >= MAX_RECONNECTS) {
        finishingRef.current = true;
        setStreaming(false);
        setReconnecting(false);
        cb.current.onError("Lost the live connection to this turn. It is still running on the server, reattaching...");
        return;
      }
      reconnectsRef.current += 1;
      setReconnecting(true);
      window.setTimeout(() => {
        if (!finishingRef.current) attachRef.current(turnId, threadId, isFirst);
      }, RECONNECT_DELAY_MS);
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) void handleDrop();
    };
  }, []);
  attachRef.current = attach;

  const stop = useCallback(() => {
    closeStream();
    liveTurnRef.current = null;
    // Claim the finish so a pending drop handler can't re-attach behind us.
    finishingRef.current = true;
    setStreaming(false);
    setReconnecting(false);
    const id = activeIdRef.current;
    if (!id) { cb.current.dispatchQueue(); return; }
    chatApi.getThread(id)
      .then((t) => { cb.current.onMessages(t.messages); setPendingSegments([]); })
      .catch(() => {
        const segs = pendingRef.current;
        if (segs.length) cb.current.appendSegmentsAsMessage(segs);
        setPendingSegments([]);
      })
      .finally(() => cb.current.dispatchQueue());
  }, [closeStream, activeIdRef]);

  // Elapsed ticker. Starts when a turn begins and keeps running across
  // reconnects (streaming stays true), so the indicator reflects the real turn
  // duration rather than the last reconnect.
  useEffect(() => {
    if (!streaming) { setElapsed(0); turnStartRef.current = 0; return; }
    if (!turnStartRef.current) turnStartRef.current = Date.now();
    const tick = () => setElapsed(Math.floor((Date.now() - turnStartRef.current) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [streaming]);

  return {
    streaming, streamingRef, logBusyRef, reconnecting, elapsed,
    pendingSegments, setPendingSegments, liveTurnRef,
    attach, anchorStart, stop, closeStream, hasOpenStream,
    markStarting, markFailed, segmentCount,
  };
}

// Liveness is server state — poll the thread while idle and re-attach the moment
// the server says a turn is live. Separate from the hook above so the caller can
// give it the thread id it is actually showing.
export function useTurnLiveness({
  activeId,
  streaming,
  hasOpenStream,
  onReattach,
}: {
  activeId: string | null;
  streaming: boolean;
  hasOpenStream: () => boolean;
  // `during`: any messages filed after the live turn's placeholder (mid-turn
  // interjections this tab never saw, e.g. sent from another tab/device).
  onReattach: (
    turnId: string, threadId: string, startedMs: number, msgs: ChatMsg[], during: ChatMsg[],
  ) => void;
}) {
  const cb = useRef(onReattach);
  cb.current = onReattach;
  useEffect(() => {
    if (!activeId || streaming) return;
    let cancelled = false;
    const check = async () => {
      if (cancelled || hasOpenStream()) return;
      try {
        const t = await chatApi.getThread(activeId);
        if (cancelled || hasOpenStream()) return;
        const live = findLiveTurn(t.messages);
        if (live) {
          const idx = t.messages.indexOf(live);
          cb.current(
            live.meta!.turn_id!, t.id, Date.parse(live.ts || ""),
            t.messages.slice(0, idx), t.messages.slice(idx + 1),
          );
        }
      } catch { /* transient — try again on the next tick */ }
    };
    const id = window.setInterval(check, LIVENESS_POLL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [activeId, streaming, hasOpenStream]);
}
