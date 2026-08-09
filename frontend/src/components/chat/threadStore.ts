import type { ChatMsg, ChatSegment } from "../../lib/chat";
import { applyStreamEvent, type StreamEvent } from "./segments";

// ONE store per thread. The client's whole model of a conversation lives here,
// and nothing else owns any part of it.
//
// This replaces five owners of the transcript that were concatenated by
// convention at render time — `messages`, `pendingSegments`, `sentDuringTurn`,
// the type-ahead queue and the voice settle. Every historical reorder,
// double-bubble and eaten-message report was a disagreement between two of the
// five, because there was no single answer to "what is in this chat".
//
// The shape mirrors the server's, which is the point:
//
//   byId + order   the committed log, keyed by event id, ordered by seq
//   cursor         the seq we have read up to; the events endpoint takes it
//   activeRun      the turn in flight, which is NOT in the log until it ends
//   pending        sends this browser has made that are not committed yet
//
// The transcript is not in here. It is derived — see transcriptBlocks.ts.

export type EventKind = "message" | "run" | "boundary" | "title";

/** One event as the server wrote it. `GET /chat/threads/{id}/events` returns
 *  these verbatim, in seq order. */
export type LogEvent = {
  seq: number;
  id: string;
  ts: string;
  kind: EventKind;
  // kind === "message"
  role?: "user" | "assistant";
  content?: string;
  client_id?: string;
  meta?: ChatMsg["meta"];
  // kind === "run"
  run_id?: string;
  state?: "started" | "finished" | "lost";
  // kind === "boundary"
  mode?: "compact" | "clear";
  summary?: string;
  // kind === "title"
  title?: string;
};

/** `active_run` as the events endpoint reports it. `live` is the server's
 *  throttled snapshot of the running turn — the fallback for a run this browser
 *  is not streaming (another tab, a reload, a dropped socket). */
export type ActiveRunPayload = {
  run_id: string;
  started_seq?: number | null;
  live?: {
    run_id?: string;
    content?: string;
    ts?: string;
    meta?: { segments?: ChatSegment[]; model?: string; agent?: string; turn_id?: string };
  } | null;
} | null;

export type ActiveRun = {
  runId: string;
  startedSeq: number | null;
  segments: ChatSegment[];
  // Epoch ms the turn began, for the elapsed clock. Anchored to the server's
  // start time whenever it is known, so a reload mid-turn shows the real
  // duration instead of restarting from zero.
  startedMs: number;
  model?: string;
  agent?: string;
};

export type PendingStatus = "sending" | "sent" | "failed";

export type PendingSend = {
  clientId: string;
  text: string;
  ts: string;
  status: PendingStatus;
  // Monotonic counter, not a timestamp: two sends in the same millisecond still
  // render in the order they were made.
  at: number;
};

export type ThreadState = {
  threadId: string | null;
  byId: Record<string, LogEvent>;
  /** Committed seqs, ascending. */
  order: number[];
  /** seq -> event id, so `order` can be walked into `byId`. */
  idBySeq: Record<number, string>;
  /** The highest seq applied. What the next sync asks for. */
  cursor: number;
  pending: Record<string, PendingSend>;
  activeRun: ActiveRun | null;
  /** Bumped on every pending write, so `at` is strictly increasing. */
  clock: number;
};

export type ThreadAction =
  | { type: "reset"; threadId: string | null }
  | {
      type: "eventsReceived";
      events: LogEvent[];
      lastSeq: number;
      activeRun: ActiveRunPayload;
    }
  | { type: "sendStarted"; clientId: string; text: string; ts: string }
  | { type: "sendAcked"; clientId: string }
  | { type: "sendFailed"; clientId: string }
  | { type: "sendRetracted"; clientId: string }
  // A turn is starting: show it as running from the moment the operator hits
  // send, not from when the POST resolves. (Not in the spec's action list; the
  // spec's `runDelta` cannot express "running, nothing produced yet", and that
  // gap is exactly the window a send used to fall through.)
  | { type: "runStarted"; runId: string; startedMs?: number; model?: string }
  | { type: "runDelta"; runId: string; event: StreamEvent }
  | { type: "runCommitted"; runId: string };

export function initialThreadState(threadId: string | null = null): ThreadState {
  return {
    threadId,
    byId: {},
    order: [],
    idBySeq: {},
    cursor: 0,
    pending: {},
    activeRun: null,
    clock: 0,
  };
}

function adoptRun(payload: ActiveRunPayload, prev: ActiveRun | null): ActiveRun | null {
  if (!payload || !payload.run_id) return null;
  const live = payload.live || {};
  const serverSegs = live.meta?.segments || [];
  const startedMs = live.ts ? Date.parse(live.ts) : NaN;
  if (prev && prev.runId === payload.run_id) {
    // THE LOCAL COPY IS THE RENDER-AHEAD ONE. The server writes its snapshot on
    // a throttle (a turn rewrites it hundreds of times, so it deliberately lags
    // the stream). Taking it unconditionally would make a live turn flicker
    // backwards every time the cursor synced. Take it only when it is AHEAD,
    // which is the case that matters: this browser is not the one streaming.
    return {
      ...prev,
      startedSeq: payload.started_seq ?? prev.startedSeq,
      segments: serverSegs.length > prev.segments.length ? serverSegs : prev.segments,
      startedMs: Number.isFinite(startedMs) ? startedMs : prev.startedMs,
      model: live.meta?.model ?? prev.model,
      agent: live.meta?.agent ?? prev.agent,
    };
  }
  return {
    runId: payload.run_id,
    startedSeq: payload.started_seq ?? null,
    segments: serverSegs,
    startedMs: Number.isFinite(startedMs) ? startedMs : Date.now(),
    model: live.meta?.model,
    agent: live.meta?.agent,
  };
}

/** Drop the optimistic copies of anything the log now carries.
 *
 *  BY CLIENT ID, never by tail index or content equality. Matching on
 *  `messages[messages.length - 1].content === text` is what the send path used
 *  to do: it popped the wrong bubble whenever the operator said the same thing
 *  twice, and it could not tell an optimistic bubble from a committed one at
 *  all. */
function reconcilePending(
  pending: Record<string, PendingSend>, events: LogEvent[],
): Record<string, PendingSend> {
  let next = pending;
  for (const ev of events) {
    const cid = ev.kind === "message" ? ev.client_id : undefined;
    if (!cid || !next[cid]) continue;
    if (next === pending) next = { ...pending };
    delete next[cid];
  }
  return next;
}

export function threadReducer(state: ThreadState, action: ThreadAction): ThreadState {
  switch (action.type) {
    case "reset":
      return initialThreadState(action.threadId);

    case "eventsReceived": {
      const byId = { ...state.byId };
      const idBySeq = { ...state.idBySeq };
      let order = state.order;
      let added = false;
      for (const ev of action.events) {
        if (!ev || typeof ev.seq !== "number" || !ev.id) continue;
        if (byId[ev.id]) continue;   // dedupe by id: a replayed cursor is free
        byId[ev.id] = ev;
        idBySeq[ev.seq] = ev.id;
        if (!added) { order = state.order.slice(); added = true; }
        order.push(ev.seq);
      }
      if (added) order.sort((a, b) => a - b);
      return {
        ...state,
        byId,
        idBySeq,
        order,
        cursor: Math.max(state.cursor, action.lastSeq || 0),
        pending: reconcilePending(state.pending, action.events),
        activeRun: adoptRun(action.activeRun, state.activeRun),
      };
    }

    case "sendStarted": {
      const clock = state.clock + 1;
      return {
        ...state,
        clock,
        pending: {
          ...state.pending,
          [action.clientId]: {
            clientId: action.clientId,
            text: action.text,
            ts: action.ts,
            status: "sending",
            at: clock,
          },
        },
      };
    }

    case "sendAcked":
    case "sendFailed": {
      const cur = state.pending[action.clientId];
      if (!cur) return state;
      const status: PendingStatus = action.type === "sendAcked" ? "sent" : "failed";
      if (cur.status === status) return state;
      return {
        ...state,
        pending: { ...state.pending, [action.clientId]: { ...cur, status } },
      };
    }

    case "sendRetracted": {
      if (!state.pending[action.clientId]) return state;
      const pending = { ...state.pending };
      delete pending[action.clientId];
      return { ...state, pending };
    }

    case "runStarted": {
      if (state.activeRun?.runId === action.runId) return state;
      return {
        ...state,
        activeRun: {
          runId: action.runId,
          startedSeq: null,
          segments: [],
          startedMs: action.startedMs ?? Date.now(),
          model: action.model,
        },
      };
    }

    case "runDelta": {
      const run = state.activeRun;
      // A delta for a run we are not showing is not ours to fold in. It arrives
      // when a stream outlives the turn it belonged to.
      if (!run || run.runId !== action.runId) return state;
      const segments = applyStreamEvent(run.segments, action.event);
      if (segments === run.segments) return state;
      return { ...state, activeRun: { ...run, segments } };
    }

    case "runCommitted": {
      // The reply is in the log now (or is about to be, on the next sync). The
      // live view has nothing left to say.
      if (!state.activeRun || state.activeRun.runId !== action.runId) return state;
      return { ...state, activeRun: null };
    }

    default:
      return state;
  }
}

/** The committed events, in seq order. */
export function committedEvents(state: ThreadState): LogEvent[] {
  const out: LogEvent[] = [];
  for (const seq of state.order) {
    const ev = state.byId[state.idBySeq[seq]];
    if (ev) out.push(ev);
  }
  return out;
}

/** Pending sends in the order they were made. */
export function pendingSends(state: ThreadState): PendingSend[] {
  return Object.values(state.pending).sort((a, b) => a.at - b.at);
}
