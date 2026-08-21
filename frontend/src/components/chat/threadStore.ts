import type { ActiveTurnPayload, ChatSegment, LogEvent, QueuedMessagePayload } from "../../lib/chat";
import { applyStreamEvent, sealTrailingText, type StreamEvent } from "./segments";

// The wire shapes live with the rest of the API surface (lib/chat); re-exported
// here because this module is where they are reasoned about.
export type { ActiveTurnPayload, EventKind, LogEvent } from "../../lib/chat";

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
//   activeTurn      the turn in flight, which is NOT in the log until it ends
//   pending        sends this browser has made that are not committed yet
//
// The transcript is not in here. It is derived — see transcriptBlocks.ts.

export type ActiveTurn = {
  turnId: string;
  startedSeq: number | null;
  segments: ChatSegment[];
  // Epoch ms the turn began, for the elapsed clock. Anchored to the server's
  // start time whenever it is known, so a reload mid-turn shows the real
  // duration instead of restarting from zero.
  startedMs: number;
  model?: string;
  agent?: string;
  // False when the run takes no input at all (a slash command, a voice turn).
  // The send path reads this to queue rather than inject; see sendPlan.
  acceptsInput?: boolean;
  // Messages that arrived before this run emitted ANYTHING, by cut token. The
  // one recorded cut position with no segment to sit on. See docs/decisions/0018.
  cutHead?: string[];
  // Epoch ms at which THIS TAB dispatched `turnStarted`, if it did. Not a display
  // value: it is what lets a sync response be dated against the run, so an
  // answer computed before the run existed cannot erase it. Absent on a run
  // this tab learned about from the server.
  localStartedAt?: number;
  // The live context occupancy the model reported on this run's latest step.
  // The composer meter prefers this while the run is in flight; the thread's
  // stored reading only updates at finalize, which is why the meter used to
  // sit still for a whole turn and then jump.
  ctxTokens?: number;
  // WHO OWNS THE SEGMENTS. True while this tab holds (or is reconnecting within
  // budget to) the turn's SSE socket: the stream is then the sole authority for
  // segments, because it replays from offset 0 on every reconnect and
  // applyStreamEvent APPENDS. A poll snapshot folded in during that window is
  // the same events a second time, which rendered the whole reply twice
  // (reported 2026-08-18). The poll advances segments ONLY when this is false,
  // which is the give-up state useTurnStream drops into after MAX_RECONNECTS,
  // where its own comment says the poll keeps the snapshot current. Set true by
  // `turnReattached` (fired before every socket open), false by
  // `turnStreamDropped` (the budget-exhausted give-up).
  streamOwned?: boolean;
};

export type PendingStatus = "sending" | "sent" | "failed";

export type PendingSend = {
  clientId: string;
  text: string;
  ts: string;
  status: PendingStatus;
  /** Why it failed, in a sentence, for rendering UNDER this message. Set only
   *  with `failed`. It lives on the send rather than in the chat's one error
   *  slot because that slot is overwritten by the next error and cleared by the
   *  next send, so the explanation for a bubble outlived the bubble by about a
   *  keystroke. See sendFailure.ts. */
  error?: string;
  /** Files this send carries, kept so a RETRY re-sends them. Without it a failed
   *  message retried as bare text and the operator had no way to see that the
   *  files had gone. */
  attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
  // Monotonic counter, not a timestamp: two sends in the same millisecond still
  // render in the order they were made.
  at: number;
  /** The last committed seq at the moment this send was made: WHERE IN THE LOG
   *  IT BELONGS.
   *
   *  A send that is still in flight is at the tail, so this is the tail and it
   *  renders last, as it always did. A FAILED one is not: the log keeps growing
   *  underneath it, and without an anchor it stayed pinned below everything,
   *  so every message sent afterwards drew ABOVE it and the conversation read
   *  backwards from there down. Reported 2026-08-11: "my new messages show
   *  above this old message, and then subsequent chat messages show above these
   *  messages". See transcriptBlocks.composeTranscript. */
  afterSeq: number;
  /** Whether a turn was already running in this thread at the moment this send
   *  began. A send made while nothing was running is what STARTS a turn, so it
   *  belongs above the working indicator once one appears; a send made while a
   *  turn was already in flight belongs below it, same as a committed mid-turn
   *  message. Read at send time, not at render time, because by the time a
   *  render happens `activeTurn` may already be set from the very turn THIS
   *  send caused. See `composeTranscript`. */
  duringActiveTurn: boolean;
};

/** A message the SERVER is holding to send next.
 *
 *  Not "pending": pending means this browser is mid-request. A queued message
 *  has been accepted, is on the thread, and will go out when the run in flight
 *  ends — whether or not this browser is here to see it. It is read, never
 *  written: the client renders the server's queue and cannot disagree with it.
 *
 *  It used to live in localStorage and be drained by whichever tab happened to
 *  be looking at the thread when the turn ended, which is how two of the
 *  operator's messages ceased to exist on 2026-08-12. See
 *  backend/services/chat_queue.py. */
export type QueuedMessage = {
  client_id: string;
  text: string;
  /** When the operator hit enter. */
  ts: string;
  attachments?: QueuedMessagePayload["attachments"];
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
  /** The server's queue for this thread, in send order. */
  queued: QueuedMessage[];
  /** Stop is holding the queue. Cleared by the operator's next send. */
  queueHeld: boolean;
  /** Why the queue is not moving, when it stopped for a reason. */
  queueError: string;
  /** THE CONTEXT CUT, AS THE SERVER RESOLVES IT: how many messages are no longer
   *  sent verbatim, and whether /clear dropped them or /compact folded them into
   *  a summary.
   *
   *  Read from the server rather than derived from the log's boundary events,
   *  and the difference is not academic. The server ignores a `compact`
   *  boundary with no summary behind it (folding was removed and restored on
   *  2026-08-12, so four such boundaries exist in real threads). A client
   *  deriving its own divider from the raw events would announce a fold on
   *  those threads while every message was still being sent — the transcript
   *  describing something that is not happening, which is the whole class of
   *  bug this chat is being rebuilt to remove. */
  cut: { count: number; mode: "clear" | "compact" } | null;
  activeTurn: ActiveTurn | null;
  /** Bumped on every pending write, so `at` is strictly increasing. */
  clock: number;
  /** Is there more history older than `order[0]`? Set from the server's
   *  `has_more` on a tail/before page ONLY (see `eventsReceived`'s `hasMore`);
   *  a plain cursor sync (`after>0`) never touches it, so an ordinary poll
   *  cannot flip the "load older" affordance off. `order[0]` doubles as the
   *  next `before` to send — the oldest seq this tab holds is exactly the
   *  floor of the last page it read, whether that page arrived as the initial
   *  tail or as an older page prepended since. */
  hasMoreOlder: boolean;
};

export type ThreadAction =
  | { type: "reset"; threadId: string | null }
  | {
      type: "eventsReceived";
      events: LogEvent[];
      lastSeq: number;
      activeTurn: ActiveTurnPayload;
      /** The server's queue, as of this response. Absent (an older backend, or
       *  a caller that has none to report) leaves what is held. */
      queued?: QueuedMessage[];
      queueHeld?: boolean;
      queueError?: string;
      /** The cut the server is APPLYING: how many messages are behind it and
       *  which command put them there. Not derived from the boundary events —
       *  see `cut` in ThreadState. */
      clearedThrough?: number;
      // A bare string, because that is what the server serves. `nextCut` below
      // is the one place that discriminates, and it already normalises anything
      // that is not "compact" to "clear".
      cutMode?: string;
      /** Epoch ms at which the REQUEST behind this response was issued. Several
       *  syncs are legitimately in flight at once (the 10s poll, focus and
       *  visibilitychange, finish()'s settle loop, useCompaction's un-awaited
       *  one), and they can resolve out of order. Without this, a response
       *  computed before a run started could land after it and blank the run. */
      requestedAt?: number;
      /** The server's `has_more`, but ONLY when this response came from a
       *  tail/before page (the first read of a thread, or a branch-change
       *  re-read). `undefined` on an ordinary cursor sync, which leaves
       *  `hasMoreOlder` exactly as it was — that page never asked the
       *  question, so it must not answer it. See `hasMoreOlder`. */
      hasMore?: boolean;
    }
  | {
      // A page further into this thread's PAST, fetched with `before` after the
      // operator (or an auto-loader) asked for more. Distinct from
      // `eventsReceived` on purpose: this never touches `cursor` (which means
      // "newest seen", and stays that way so incremental sync is untouched),
      // never reconciles pending sends (nothing here can be this tab's own
      // send — a send is always at the tail), and always PREPENDS rather than
      // appends.
      type: "olderEventsReceived";
      events: LogEvent[];
      /** Whether the server says there is more behind even this page. */
      hasMore: boolean;
    }
  | {
      type: "sendStarted"; clientId: string; text: string; ts: string;
      /** Files this send carries, so a retry re-sends them instead of turning
       *  into the same message with its attachments quietly gone. */
      attachments?: PendingSend["attachments"];
    }
  | { type: "sendAcked"; clientId: string }
  | { type: "sendFailed"; clientId: string; error?: string }
  | { type: "sendRetracted"; clientId: string }
  // The OPERATOR pulled a still-local send back, from its X. Same effect on
  // `pending` as `sendRetracted` (the outbox mirror is keyed off that map, not
  // off which action emptied it), but a distinct type: `sendRetracted` means
  // the server took the message over onto its own queue, and conflating the
  // two would say a message the operator discarded is still owed somewhere.
  | { type: "sendCancelled"; clientId: string }
  // Un-acknowledged sends read back from durable storage on mount. THE
  // REDUCER'S `pending` DOES NOT SURVIVE A RELOAD on its own, and a send the
  // server never took is text the operator hit enter on: it has to come back.
  // See useSendOutbox — this is the restore half of that mirror.
  | { type: "pendingRestored"; sends: PendingSend[] }
  // A turn is starting: show it as running from the moment the operator hits
  // send, not from when the POST resolves. (Not in the spec's action list; the
  // spec's `turnDelta` cannot express "running, nothing produced yet", and that
  // gap is exactly the window a send used to fall through.)
  | { type: "turnStarted"; turnId: string; startedMs?: number; model?: string }
  | { type: "turnDelta"; turnId: string; event: StreamEvent }
  // This tab is opening a FRESH socket for a run it is already showing. The
  // stream is not resumable from a new EventSource — it carries no
  // Last-Event-ID, so the server replays the turn from offset 0 — and
  // applyStreamEvent APPENDS, so folding that replay into segments this tab
  // already holds renders the whole turn twice. Rewind first; the replay puts it
  // back. Triggers: a backend restart, an edge 502 mid-turn, and the 5s liveness
  // re-attach, so this fires on the ordinary long turn, not an exotic one.
  | { type: "turnReattached"; turnId: string }
  // The stream ran out of reconnect budget and let go of the socket. The turn
  // is still in flight; the poll takes over as the segment source now, so this
  // hands ownership back to it (see `streamOwned`).
  | { type: "turnStreamDropped"; turnId: string }
  | { type: "turnContext"; turnId: string; tokens: number }
  | { type: "turnCommitted"; turnId: string }
  // Adopt a PREVIOUSLY-HELD snapshot of this same thread in one commit, so a
  // revisited thread paints instantly instead of blank-then-loaded. See
  // threadCache.ts for what holds the snapshot between visits and why.
  //
  // Deliberately narrower than `eventsReceived`: it carries only the fields a
  // cache can honestly know. `pending`/`clock` are excluded on purpose —
  // useSendOutbox is the ONE owner of unacknowledged sends (see its header
  // comment) and restores them itself, per thread, from durable storage. A
  // cache that also seeded `pending` would be a second owner of the same
  // question, which is exactly the class of bug this store exists to remove.
  | { type: "seedThread"; threadId: string; snapshot: ThreadSnapshot };

/** The subset of `ThreadState` a cache can honestly reseed: the committed log,
 *  the cursor it was read up to, the live run, the server's queue and the
 *  context cut. NOT `pending` or `clock` — see `seedThread` above. */
export type ThreadSnapshot = Pick<
  ThreadState,
  "threadId" | "byId" | "order" | "idBySeq" | "cursor" | "cut" | "activeTurn"
  | "queued" | "queueHeld" | "queueError" | "hasMoreOlder"
>;

/** Lift the cacheable half of a thread's state out, by reference. Nothing here
 *  is cloned: the reducer already treats every one of these fields as
 *  immutable (a new array/object only when it actually changed), so holding
 *  the same references in a side cache is free and safe. */
export function snapshotThread(state: ThreadState): ThreadSnapshot {
  return {
    threadId: state.threadId,
    byId: state.byId,
    order: state.order,
    idBySeq: state.idBySeq,
    cursor: state.cursor,
    cut: state.cut,
    activeTurn: state.activeTurn,
    queued: state.queued,
    queueHeld: state.queueHeld,
    queueError: state.queueError,
    hasMoreOlder: state.hasMoreOlder,
  };
}

export function initialThreadState(threadId: string | null = null): ThreadState {
  return {
    threadId,
    byId: {},
    order: [],
    idBySeq: {},
    cursor: 0,
    pending: {},
    queued: [],
    queueHeld: false,
    queueError: "",
    cut: null,
    activeTurn: null,
    clock: 0,
    hasMoreOlder: false,
  };
}

/** A cheap monotonic size for one segment. Only the ORDER of two of these
 *  matters, never the unit: a segment that grew must weigh more than it did. */
function segWeight(s: ChatSegment | undefined): number {
  if (!s) return 0;
  if (s.t === "text") return s.text.length;
  if (s.t === "tool") return s.name.length + s.input.length;
  // An approval changes weight exactly once, when it is answered, so its state
  // is the whole signal. Without it a resolved approval looks identical to a
  // pending one and the snapshot never overtakes the local render.
  if (s.t === "approval") return s.label.length + s.state.length;
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

function adoptTurn(
  payload: ActiveTurnPayload, prev: ActiveTurn | null, requestedAt: number,
): ActiveTurn | null {
  // A RESPONSE CANNOT SPEAK ABOUT A RUN THAT DID NOT EXIST WHEN IT WAS ASKED
  // FOR. Syncs overlap by design and resolve out of order, so an older snapshot
  // arriving after a locally-dispatched `turnStarted` used to clear the live
  // block outright — after which the reducer's own guard dropped that run's
  // streamed deltas as "not ours", the turn went invisible until the next 10s
  // poll re-adopted it, and in that window canDrainQueue was true, so a queued
  // message could open a rival turn. The trigger is real and routine:
  // useCompaction fires an un-awaited sync() immediately before sending
  // /compact.
  const stale = !!prev?.localStartedAt && requestedAt < prev.localStartedAt;
  if (!payload || !payload.turn_id) return stale ? prev : null;
  // Same reasoning for a snapshot that names a DIFFERENT, older run.
  if (stale && prev && payload.turn_id !== prev.turnId) return prev;
  const live = payload.live || {};
  const serverSegs = live.meta?.segments || [];
  const startedMs = live.ts ? Date.parse(live.ts) : NaN;
  if (prev && prev.turnId === payload.turn_id) {
    // An answer to a question asked before this tab's own last move — the run
    // starting, or a re-attach rewinding the live view — cannot speak about the
    // state that move produced. Keep what we have and wait for a current one.
    if (stale) return prev;
    // THE STREAM OWNS SEGMENTS WHILE IT IS ATTACHED. A turn this tab is
    // streaming replays from offset 0 on every socket open and appends, so a
    // poll snapshot folded in during that window is the reply a second time
    // (the doubling of 2026-08-18). So the poll advances segments ONLY when the
    // stream has GIVEN UP (`streamOwned` false), the give-up state whose own
    // comment says the poll keeps the snapshot current. Even then, take the
    // server's copy only when it is AHEAD: the snapshot is throttled and lags
    // the stream, and taking it unconditionally flickers a live turn backwards.
    const takeSegs = !prev.streamOwned && snapshotAhead(serverSegs, prev.segments);
    return {
      ...prev,
      startedSeq: payload.started_seq ?? prev.startedSeq,
      segments: takeSegs ? serverSegs : prev.segments,
      // Same rule as the segments: the streaming tab learns these from the `cut`
      // event and owns them; the poll only supplies them once the stream has
      // given up, and only when its copy is longer.
      cutHead: (!prev.streamOwned && (live.meta?.cut_head?.length ?? 0) > (prev.cutHead?.length ?? 0))
        ? live.meta?.cut_head : prev.cutHead,
      startedMs: Number.isFinite(startedMs) ? startedMs : prev.startedMs,
      model: live.meta?.model ?? prev.model,
      agent: live.meta?.agent ?? prev.agent,
      acceptsInput: payload.accepts_input ?? prev.acceptsInput,
    };
  }
  return {
    turnId: payload.turn_id,
    startedSeq: payload.started_seq ?? null,
    segments: serverSegs,
    cutHead: live.meta?.cut_head,
    startedMs: Number.isFinite(startedMs) ? startedMs : Date.now(),
    model: live.meta?.model,
    agent: live.meta?.agent,
    // The wire says `boolean | null`; absent and null both mean "not stated",
    // which this store spells `undefined`.
    acceptsInput: payload.accepts_input ?? undefined,
  };
}

/** The cut, kept referentially stable when it has not moved: it feeds the
 *  committed-transcript memo, which must not re-derive on every 10s poll. */
function nextCut(
  prev: ThreadState["cut"], a: { clearedThrough?: number; cutMode?: string },
): ThreadState["cut"] {
  if (a.clearedThrough === undefined) return prev;
  if (!a.clearedThrough) return prev === null ? prev : null;
  const mode = a.cutMode === "compact" ? "compact" : "clear";
  if (prev && prev.count === a.clearedThrough && prev.mode === mode) return prev;
  return { count: a.clearedThrough, mode };
}

/** Is the server's queue the one already rendered?
 *
 *  Compared by identity and order, not deep-equality: the queue is empty on
 *  almost every poll, and holding the SAME array reference when nothing changed
 *  is what keeps the transcript memo from re-deriving on a 10s timer forever. */
function sameQueue(prev: QueuedMessage[], next: QueuedMessage[] | undefined): boolean {
  if (!next) return true;
  if (prev.length !== next.length) return false;
  for (let i = 0; i < prev.length; i++) {
    if (prev[i].client_id !== next[i].client_id || prev[i].text !== next[i].text) return false;
  }
  return true;
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
  queued?: QueuedMessage[],
): Record<string, PendingSend> {
  let next = pending;
  const drop = (cid: string | undefined) => {
    if (!cid || !next[cid]) return;
    if (next === pending) next = { ...pending };
    delete next[cid];
  };
  for (const ev of events) drop(ev.kind === "message" ? ev.client_id : undefined);
  // ...AND ANYTHING THE SERVER'S QUEUE NOW HOLDS. A send whose response was lost
  // in flight (the request landed, the answer did not) leaves this tab thinking
  // the message is still its problem while the server has already taken it. Both
  // would draw, the same text twice. The queue naming it is proof of delivery,
  // exactly as a committed event is.
  for (const q of queued || []) drop(q.client_id);
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
      // NOTHING IS COPIED UNTIL SOMETHING IS ACTUALLY NEW.
      //
      // A poll that returns no events is the COMMON case: the chat polls every
      // 10s whether or not anything happened, and a re-fetch is deliberately
      // cheap and idempotent, so replayed pages are normal too. This used to
      // spread both maps and re-sort `order` on every one of those, which is
      // O(thread) of pure waste per poll, forever, on a thread that only grows.
      //
      // Holding the original references when nothing was added also keeps their
      // IDENTITY stable, which is what the committed-transcript memo in
      // useThreadLog keys on: an empty poll now costs the render path nothing
      // at all instead of re-deriving every message in the thread.
      let byId = state.byId;
      let idBySeq = state.idBySeq;
      let order = state.order;
      let added = false;
      // Whether anything arrived before an event already held. The log is
      // append-only and the server sends it in seq order, so this is normally
      // false and the sort can be skipped: checking is O(new), sorting is
      // O(n log n) over the whole thread.
      let outOfOrder = false;
      // Runs a message just landed in the middle of. Collected from events this
      // batch is seeing for the FIRST time: sealing is a cut at "here, now", so
      // doing it again for a replayed cursor would cut a second time, further
      // down, at a point no message ever arrived at.
      const interrupted: string[] = [];
      for (const ev of action.events) {
        if (!ev || typeof ev.seq !== "number" || !ev.id) continue;
        if (byId[ev.id]) continue;   // dedupe by id: a replayed cursor is free
        if (!added) {
          byId = { ...state.byId };
          idBySeq = { ...state.idBySeq };
          order = state.order.slice();
          added = true;
        }
        byId[ev.id] = ev;
        idBySeq[ev.seq] = ev.id;
        if (order.length && ev.seq < order[order.length - 1]) outOfOrder = true;
        order.push(ev.seq);
        if (ev.kind === "message" && ev.role === "user" && ev.meta?.during_turn) {
          interrupted.push(ev.meta.during_turn);
        }
      }
      if (outOfOrder) order.sort((a, b) => a - b);
      // No `requestedAt` means the caller cannot date its response, so it is
      // treated as current — the guard only ever suppresses a demonstrably
      // older answer.
      let activeTurn = adoptTurn(
        action.activeTurn, state.activeTurn, action.requestedAt ?? Date.now());
      // The live half of the cut. The server seals the turn it is driving; this
      // seals what THIS tab is rendering, on the same trigger, so a message
      // typed in another tab still closes the block here.
      if (activeTurn && interrupted.includes(activeTurn.turnId)) {
        activeTurn = { ...activeTurn, segments: sealTrailingText(activeTurn.segments) };
      }
      return {
        ...state,
        byId,
        idBySeq,
        order,
        cursor: Math.max(state.cursor, action.lastSeq || 0),
        // THE SERVER'S QUEUE REPLACES OURS WHOLESALE. There is nothing to merge:
        // it is not this client's state, it is a fact about the thread, and a
        // client that "merged" it could show a message the server has already
        // sent or hide one another device just queued. Identity is stable
        // (client_id), so React keys survive the swap.
        queued: sameQueue(state.queued, action.queued) ? state.queued
          : (action.queued ?? state.queued),
        queueHeld: action.queueHeld ?? state.queueHeld,
        queueError: action.queueError ?? state.queueError,
        cut: nextCut(state.cut, action),
        // A message that made it out of the queue and into the log is no longer
        // pending here either: it was OUR send, arriving late.
        pending: reconcilePending(state.pending, action.events, action.queued),
        activeTurn,
        hasMoreOlder: action.hasMore !== undefined ? action.hasMore : state.hasMoreOlder,
      };
    }

    case "olderEventsReceived": {
      // Same dedupe-by-id and no-copy-until-new discipline as `eventsReceived`,
      // mirrored for the front of the array: an empty or fully-replayed page
      // (a retried `before` after a network error, say) costs nothing.
      let byId = state.byId;
      let idBySeq = state.idBySeq;
      let added = false;
      const newSeqs: number[] = [];
      for (const ev of action.events) {
        if (!ev || typeof ev.seq !== "number" || !ev.id) continue;
        if (byId[ev.id]) continue;
        if (!added) {
          byId = { ...state.byId };
          idBySeq = { ...state.idBySeq };
          added = true;
        }
        byId[ev.id] = ev;
        idBySeq[ev.seq] = ev.id;
        newSeqs.push(ev.seq);
      }
      if (!added) {
        return state.hasMoreOlder === action.hasMore
          ? state : { ...state, hasMoreOlder: action.hasMore };
      }
      // The page arrives newest-first-of-the-batch in seq order already (the
      // server reads it off the same seq-ordered log), but sorted defensively
      // rather than assumed, the same as `eventsReceived` does for the tail.
      newSeqs.sort((a, b) => a - b);
      return {
        ...state, byId, idBySeq,
        order: [...newSeqs, ...state.order],
        hasMoreOlder: action.hasMore,
      };
    }

    case "pendingRestored": {
      // Anything this tab already holds wins: it is live, and the restored copy
      // is by definition older. Restoring is for a fresh mount, where `pending`
      // is empty and the durable copy is all there is.
      let clock = state.clock;
      const pending = { ...state.pending };
      for (const s of action.sends) {
        if (pending[s.clientId]) continue;
        pending[s.clientId] = { ...s, at: ++clock };
      }
      if (clock === state.clock) return state;
      return { ...state, pending, clock };
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
            ...(action.attachments?.length ? { attachments: action.attachments } : {}),
            status: "sending",
            at: clock,
            // Read here rather than passed in: the store is the only thing that
            // knows the log's tail, and a caller computing it would be reading
            // the same state one render late.
            afterSeq: lastSeq(state),
            duringActiveTurn: !!state.activeTurn,
          },
        },
      };
    }

    case "sendAcked":
    case "sendFailed": {
      const cur = state.pending[action.clientId];
      if (!cur) return state;
      const status: PendingStatus = action.type === "sendAcked" ? "sent" : "failed";
      const error = action.type === "sendFailed" ? action.error : undefined;
      if (cur.status === status && cur.error === error) return state;
      return {
        ...state,
        pending: { ...state.pending, [action.clientId]: { ...cur, status, error } },
      };
    }

    case "sendRetracted":
    case "sendCancelled": {
      if (!state.pending[action.clientId]) return state;
      const pending = { ...state.pending };
      delete pending[action.clientId];
      return { ...state, pending };
    }

    case "turnStarted": {
      if (state.activeTurn?.turnId === action.turnId) return state;
      return {
        ...state,
        activeTurn: {
          turnId: action.turnId,
          startedSeq: null,
          segments: [],
          startedMs: action.startedMs ?? Date.now(),
          localStartedAt: Date.now(),
          model: action.model,
        },
      };
    }

    case "turnDelta": {
      const run = state.activeTurn;
      // A delta for a run we are not showing is not ours to fold in. It arrives
      // when a stream outlives the turn it belonged to.
      if (!run || run.turnId !== action.turnId) return state;
      // A CUT IS A POSITION, not content, so it is recorded rather than folded.
      // Sealing first is what makes the boundary real: text streamed afterwards
      // must start a new segment instead of merging back above the message. The
      // server does exactly this to its own copy, in one call; see
      // build_streaming.cut_here.
      if (action.event.kind === "cut") {
        const ref = action.event.ref || "";
        // The replay on re-attach re-delivers every event from offset 0, so
        // this has to be idempotent or a reconnect doubles every cut.
        if (!ref) return state;
        const sealed = sealTrailingText(run.segments);
        const last = sealed[sealed.length - 1];
        if (!last) {
          if (run.cutHead?.includes(ref)) return state;
          return { ...state, activeTurn: { ...run, cutHead: [...(run.cutHead || []), ref] } };
        }
        if (last.cut_after?.includes(ref)) return state;
        return { ...state, activeTurn: { ...run, segments: [
          ...sealed.slice(0, -1),
          { ...last, cut_after: [...(last.cut_after || []), ref] },
        ] } };
      }
      const segments = applyStreamEvent(run.segments, action.event);
      if (segments === run.segments) return state;
      return { ...state, activeTurn: { ...run, segments } };
    }

    case "turnContext": {
      const run = state.activeTurn;
      if (!run || run.turnId !== action.turnId) return state;
      if (!(action.tokens > 0) || action.tokens === run.ctxTokens) return state;
      return { ...state, activeTurn: { ...run, ctxTokens: action.tokens } };
    }

    case "turnReattached": {
      const run = state.activeTurn;
      if (!run || run.turnId !== action.turnId) return state;
      return {
        ...state,
        activeTurn: {
          ...run,
          segments: [],
          // A socket just opened for this turn: the stream is now the sole
          // authority for its segments until it gives up. See `streamOwned` and
          // adoptTurn.
          streamOwned: true,
          // The head cuts came from the same replayed stream and are re-sent
          // with it. Left behind, a re-attach would keep a message pinned above
          // work the replay puts back above it.
          cutHead: [],
          // Re-stamped so `adoptTurn` treats a sync ISSUED BEFORE this rewind as
          // what it is: an answer about a live view that no longer exists.
          // Without it, an in-flight poll could land between the rewind and the
          // replay, restore a full snapshot, and have the replay append onto it
          // anyway.
          localStartedAt: Date.now(),
        },
      };
    }

    case "turnStreamDropped": {
      const run = state.activeTurn;
      if (!run || run.turnId !== action.turnId || !run.streamOwned) return state;
      // The stream let go; the poll is the segment source until a socket
      // re-attaches (which dispatches `turnReattached` and takes ownership
      // back). Segments are left as they are: the next poll advances them.
      return { ...state, activeTurn: { ...run, streamOwned: false } };
    }

    case "seedThread": {
      // A SNAPSHOT MAY NEVER BE ADOPTED UNDER A DIFFERENT THREAD'S ID. The
      // cache is keyed on threadId already (see threadCache.ts), so this is
      // belt-and-braces against a caller passing the wrong pair; on a
      // mismatch this is just an ordinary reset; no data from the mismatched
      // snapshot is ever assigned to `state`.
      if (action.snapshot.threadId !== action.threadId) return initialThreadState(action.threadId);
      return { ...initialThreadState(action.threadId), ...action.snapshot };
    }

    case "turnCommitted": {
      // The reply is in the log now (or is about to be, on the next sync). The
      // live view has nothing left to say.
      if (!state.activeTurn || state.activeTurn.turnId !== action.turnId) return state;
      return { ...state, activeTurn: null };
    }

    default:
      return state;
  }
}

/** The highest committed seq, or 0 on an empty thread. */
export function lastSeq(state: ThreadState): number {
  return state.order.length ? state.order[state.order.length - 1] : 0;
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
