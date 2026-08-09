import type { ActiveRunPayload, ChatSegment, LogEvent } from "../../lib/chat";
import { applyStreamEvent, type StreamEvent } from "./segments";

// The wire shapes live with the rest of the API surface (lib/chat); re-exported
// here because this module is where they are reasoned about.
export type { ActiveRunPayload, EventKind, LogEvent } from "../../lib/chat";

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
  // Epoch ms at which THIS TAB dispatched `runStarted`, if it did. Not a display
  // value: it is what lets a sync response be dated against the run, so an
  // answer computed before the run existed cannot erase it. Absent on a run
  // this tab learned about from the server.
  localStartedAt?: number;
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
      /** Epoch ms at which the REQUEST behind this response was issued. Several
       *  syncs are legitimately in flight at once (the 10s poll, focus and
       *  visibilitychange, finish()'s settle loop, useCompaction's un-awaited
       *  one), and they can resolve out of order. Without this, a response
       *  computed before a run started could land after it and blank the run. */
      requestedAt?: number;
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

/** A cheap monotonic size for one segment. Only the ORDER of two of these
 *  matters, never the unit: a segment that grew must weigh more than it did. */
function segWeight(s: ChatSegment | undefined): number {
  if (!s) return 0;
  if (s.t === "text") return s.text.length;
  if (s.t === "tool") return s.name.length + s.input.length;
  // A subagent grows in place three ways — another step, a step of its own that
  // got longer, and finally its result — so this recurses rather than counting.
  let w = 1 + s.result.length + (s.done ? 1 : 0);
  for (const step of s.steps) w += segWeight(step);
  return w;
}

/** Is the server's snapshot ahead of what this tab already renders?
 *
 *  SEGMENT COUNT ALONE CANNOT ANSWER THIS. The streaming text segment is
 *  appended to, not replaced (the backend's `_seg_text` merges into the trailing
 *  text segment, and this client's `pushText` mirrors it), so a turn producing
 *  one long answer never increases the count. A tab not holding the socket then
 *  showed the first partial sentence for the whole turn while the elapsed clock
 *  kept ticking. Compare the tail's size when the counts tie. */
function snapshotAhead(next: ChatSegment[], prev: ChatSegment[]): boolean {
  if (next.length !== prev.length) return next.length > prev.length;
  return segWeight(next[next.length - 1]) > segWeight(prev[prev.length - 1]);
}

function adoptRun(
  payload: ActiveRunPayload, prev: ActiveRun | null, requestedAt: number,
): ActiveRun | null {
  // A RESPONSE CANNOT SPEAK ABOUT A RUN THAT DID NOT EXIST WHEN IT WAS ASKED
  // FOR. Syncs overlap by design and resolve out of order, so an older snapshot
  // arriving after a locally-dispatched `runStarted` used to clear the live
  // block outright — after which the reducer's own guard dropped that run's
  // streamed deltas as "not ours", the turn went invisible until the next 10s
  // poll re-adopted it, and in that window canDrainQueue was true, so a queued
  // message could open a rival turn. The trigger is real and routine:
  // useCompaction fires an un-awaited sync() immediately before sending
  // /compact.
  const stale = !!prev?.localStartedAt && requestedAt < prev.localStartedAt;
  if (!payload || !payload.run_id) return stale ? prev : null;
  // Same reasoning for a snapshot that names a DIFFERENT, older run.
  if (stale && prev && payload.run_id !== prev.runId) return prev;
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
      segments: snapshotAhead(serverSegs, prev.segments) ? serverSegs : prev.segments,
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
    case "reset": {
      const fresh = initialThreadState(action.threadId);
      // A BLANK CHAT BECOMING A THREAD KEEPS ITS PENDING SEND. That reset is not
      // a thread switch; it is the first message of this conversation creating
      // the conversation, and the bubble the operator just typed must not blink
      // out between the POST and the first sync. Any other reset drops them: a
      // pending send belongs to the thread it was typed into, and leaking one
      // across a switch would show it under someone else's transcript.
      if (state.threadId === null && action.threadId !== null) {
        return { ...fresh, pending: state.pending, clock: state.clock };
      }
      return fresh;
    }

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
        // No `requestedAt` means the caller cannot date its response, so it is
        // treated as current — the guard only ever suppresses a demonstrably
        // older answer.
        activeRun: adoptRun(action.activeRun, state.activeRun, action.requestedAt ?? Date.now()),
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
          localStartedAt: Date.now(),
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
