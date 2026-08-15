import { describe, expect, it } from "vitest";
import {
  committedEvents, initialThreadState, pendingSends, threadReducer,
  type LogEvent, type ThreadState,
} from "./threadStore";

const ev = (seq: number, over: Partial<LogEvent> = {}): LogEvent => ({
  seq, id: `e${seq}`, ts: "2026-08-09T12:00:00Z", kind: "message",
  role: "user", content: `m${seq}`, ...over,
});

const apply = (state: ThreadState, ...actions: Parameters<typeof threadReducer>[1][]) =>
  actions.reduce(threadReducer, state);

describe("eventsReceived", () => {
  it("keeps events in seq order however they arrive", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(3), ev(1)], lastSeq: 3, activeRun: null },
      { type: "eventsReceived", events: [ev(2)], lastSeq: 3, activeRun: null });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("dedupes by event id, so a replayed cursor is free", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeRun: null },
      { type: "eventsReceived", events: [ev(1), ev(2), ev(3)], lastSeq: 3, activeRun: null });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("advances the cursor and never walks it backwards", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(9)], lastSeq: 9, activeRun: null },
      { type: "eventsReceived", events: [], lastSeq: 4, activeRun: null });
    expect(s.cursor).toBe(9);
  });
});

describe("hasMoreOlder", () => {
  it("is set by a fresh (tail) page that says hasMore", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeRun: null, hasMore: true });
    expect(s.hasMoreOlder).toBe(true);
  });

  it("is left alone by an ordinary cursor sync, which never carries hasMore", () => {
    // The bug this guards: a page fetched with `after=cursor` (every poll once
    // a thread has loaded) always answers `has_more: false` server-side,
    // because it never asked the question. Applying that unconditionally would
    // flip the "load older" affordance off on the very next poll after the
    // initial tail load turned it on.
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeRun: null, hasMore: true },
      { type: "eventsReceived", events: [ev(21)], lastSeq: 21, activeRun: null });
    expect(s.hasMoreOlder).toBe(true);
  });

  it("a fresh page can also turn it off, once nothing more is behind it", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeRun: null, hasMore: true },
      { type: "reset", threadId: "t2" },
      { type: "eventsReceived", events: [ev(5)], lastSeq: 5, activeRun: null, hasMore: false });
    expect(s.hasMoreOlder).toBe(false);
  });
});

describe("olderEventsReceived", () => {
  it("prepends a page of history in ascending seq order", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20), ev(21)], lastSeq: 21, activeRun: null },
      { type: "olderEventsReceived", events: [ev(18), ev(19)], hasMore: true });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([18, 19, 20, 21]);
    expect(s.hasMoreOlder).toBe(true);
  });

  it("does not duplicate an event the page already holds", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(19), ev(20)], lastSeq: 20, activeRun: null },
      { type: "olderEventsReceived", events: [ev(19), ev(18)], hasMore: false });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([18, 19, 20]);
  });

  it("never moves the cursor, so incremental sync is untouched", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeRun: null },
      { type: "olderEventsReceived", events: [ev(1)], hasMore: false });
    expect(s.cursor).toBe(20);
  });

  it("sets hasMoreOlder false once the server says there is nothing further back", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeRun: null, hasMore: true },
      { type: "olderEventsReceived", events: [ev(1)], hasMore: false });
    expect(s.hasMoreOlder).toBe(false);
  });

  it("does not reconcile pending sends -- nothing in a backward page can be this tab's own send", () => {
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "c1", text: "hello", ts: "t" },
      { type: "olderEventsReceived", events: [ev(1)], hasMore: false });
    expect(pendingSends(s).map((p) => p.clientId)).toEqual(["c1"]);
  });
});

describe("optimistic sends reconcile by client id", () => {
  it("drops the pending copy when its committed event arrives", () => {
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "c1", text: "hello", ts: "t" },
      { type: "eventsReceived", events: [ev(1, { content: "hello", client_id: "c1" })], lastSeq: 1, activeRun: null });
    expect(pendingSends(s)).toEqual([]);
  });

  it("keeps a pending send whose text also appears in the log under another id", () => {
    // The exact case content-equality got wrong: the operator said the same
    // thing twice, and the tail match popped the bubble for the wrong one.
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "c2", text: "hello", ts: "t" },
      { type: "eventsReceived", events: [ev(1, { content: "hello", client_id: "c1" })], lastSeq: 1, activeRun: null });
    expect(pendingSends(s).map((p) => p.clientId)).toEqual(["c2"]);
  });

  it("marks a failed send without dropping it, so it can be retried", () => {
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "c1", text: "hello", ts: "t" },
      { type: "sendFailed", clientId: "c1" });
    expect(pendingSends(s)[0].status).toBe("failed");
  });

  it("orders pending sends by when they were made, not by id", () => {
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "zzz", text: "first", ts: "t" },
      { type: "sendStarted", clientId: "aaa", text: "second", ts: "t" });
    expect(pendingSends(s).map((p) => p.text)).toEqual(["first", "second"]);
  });
});

describe("the active run", () => {
  const started = { type: "runStarted" as const, runId: "r1" };

  it("folds stream events into the live segments", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "hi " } },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "there" } });
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "hi there" }]);
  });

  it("ignores a delta from a run it is not showing", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "runDelta", runId: "other", event: { kind: "text", content: "x" } });
    expect(s.activeRun?.segments).toEqual([]);
  });

  it("does not let the server's throttled snapshot walk a live turn backwards", () => {
    // The snapshot is written every ~0.4s and deliberately lags the stream.
    // Taking it unconditionally made the transcript flicker on every sync.
    const s = apply(initialThreadState("t"), started,
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "a lot of text" } },
      {
        type: "eventsReceived", events: [], lastSeq: 0,
        activeRun: { run_id: "r1", started_seq: 4, live: { meta: { segments: [] } } },
      });
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "a lot of text" }]);
    expect(s.activeRun?.startedSeq).toBe(4);
  });

  it("takes the snapshot when it is ahead, which is how another tab reattaches", () => {
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeRun: {
        run_id: "r1", started_seq: 4,
        live: { meta: { segments: [{ t: "text", text: "from the server" }] } },
      },
    });
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "from the server" }]);
  });

  it("takes a snapshot that grew the SAME segment, not just a longer list", () => {
    // A turn producing one long answer never adds a second segment: the text is
    // appended to the trailing one. Counting segments made a tab that is not
    // holding the socket freeze on the first partial sentence for the whole
    // turn, elapsed clock still ticking.
    const snapshot = (text: string) => ({
      type: "eventsReceived" as const, events: [], lastSeq: 0,
      activeRun: { run_id: "r1", started_seq: 4, live: { meta: { segments: [{ t: "text" as const, text }] } } },
    });
    const s = apply(initialThreadState("t"), started,
      snapshot("The first part"), snapshot("The first part, and the rest of it"));
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "The first part, and the rest of it" }]);
  });

  it("still refuses a snapshot whose tail is shorter than what is rendered", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "a long streamed answer" } },
      {
        type: "eventsReceived", events: [], lastSeq: 0,
        activeRun: { run_id: "r1", started_seq: 4, live: { meta: { segments: [{ t: "text", text: "a long" }] } } },
      });
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "a long streamed answer" }]);
  });

  it("sees a subagent that grew in place under an unchanged segment count", () => {
    const withSteps = (steps: { t: "text"; text: string }[]) => ({
      type: "eventsReceived" as const, events: [], lastSeq: 0,
      activeRun: {
        run_id: "r1", started_seq: 4,
        live: { meta: { segments: [{
          t: "agent" as const, name: "explore", input: "look", id: "a1",
          steps, done: false, result: "",
        }] } },
      },
    });
    const s = apply(initialThreadState("t"), started,
      withSteps([{ t: "text", text: "reading" }]),
      withSteps([{ t: "text", text: "reading" }, { t: "text", text: "found it" }]));
    expect((s.activeRun?.segments[0] as { steps: unknown[] }).steps).toHaveLength(2);
  });

  it("clears when the server says no run is active", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "eventsReceived", events: [], lastSeq: 0, activeRun: null });
    expect(s.activeRun).toBeNull();
  });

  it("ignores a sync answer computed before this tab started the run", () => {
    // useCompaction fires an un-awaited sync() immediately before sending
    // /compact, so a response that predates the run routinely lands after it.
    // Clearing on that answer blanked the live block, made the reducer drop the
    // run's own streamed deltas, and left canDrainQueue true long enough for a
    // queued message to open a rival turn.
    const before = Date.now() - 5_000;
    const s = apply(initialThreadState("t"), started,
      { type: "eventsReceived", events: [], lastSeq: 0, activeRun: null, requestedAt: before });
    expect(s.activeRun?.runId).toBe("r1");
  });

  it("ignores a stale answer that names some OTHER run", () => {
    const before = Date.now() - 5_000;
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt: before,
      activeRun: { run_id: "r0", started_seq: 1, live: { meta: { segments: [] } } },
    });
    expect(s.activeRun?.runId).toBe("r1");
  });

  it("still clears on an answer asked for after the run started", () => {
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeRun: null, requestedAt: Date.now() + 5_000,
    });
    expect(s.activeRun).toBeNull();
  });

  it("clears on commit, because the reply is in the log now", () => {
    const s = apply(initialThreadState("t"), started, { type: "runCommitted", runId: "r1" });
    expect(s.activeRun).toBeNull();
  });
});

describe("the live context reading", () => {
  const started = { type: "runStarted" as const, runId: "r1" };

  it("lands on the active run and dedupes an unchanged value", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "runContext", runId: "r1", tokens: 1000 });
    expect(s.activeRun?.ctxTokens).toBe(1000);
    const same = threadReducer(s, { type: "runContext", runId: "r1", tokens: 1000 });
    expect(same).toBe(s); // no state churn on an unchanged reading
    const moved = threadReducer(s, { type: "runContext", runId: "r1", tokens: 2500 });
    expect(moved.activeRun?.ctxTokens).toBe(2500);
  });

  it("ignores a reading for a run it is not showing, and junk values", () => {
    const s0 = apply(initialThreadState("t"), started);
    expect(threadReducer(s0, { type: "runContext", runId: "other", tokens: 5 })).toBe(s0);
    expect(threadReducer(s0, { type: "runContext", runId: "r1", tokens: 0 })).toBe(s0);
  });

  it("survives the reattach rewind that empties the segments", () => {
    // The replay re-delivers every context event in order anyway, but the
    // reading must not blank in the meantime: the meter renders from it.
    const s = apply(initialThreadState("t"), started,
      { type: "runContext", runId: "r1", tokens: 1000 },
      { type: "runReattached", runId: "r1" });
    expect(s.activeRun?.ctxTokens).toBe(1000);
  });
});

describe("reset", () => {
  it("drops everything when switching to another conversation", () => {
    const s = apply(initialThreadState("a"),
      { type: "eventsReceived", events: [ev(1)], lastSeq: 1, activeRun: null },
      { type: "sendStarted", clientId: "c1", text: "mine", ts: "t" },
      { type: "reset", threadId: "b" });
    expect(committedEvents(s)).toEqual([]);
    expect(pendingSends(s)).toEqual([]);
    expect(s.threadId).toBe("b");
  });

  it("keeps the pending send when a blank chat becomes a thread", () => {
    // Not a switch: the first message of this conversation is what CREATED the
    // conversation, and the bubble must not blink out between the POST and the
    // first sync.
    const s = apply(initialThreadState(null),
      { type: "sendStarted", clientId: "c1", text: "the first thing I said", ts: "t" },
      { type: "reset", threadId: "new-thread" });
    expect(pendingSends(s).map((p) => p.text)).toEqual(["the first thing I said"]);
    expect(s.threadId).toBe("new-thread");
  });

  it("still clears when a thread is closed back to blank", () => {
    const s = apply(initialThreadState("a"),
      { type: "sendStarted", clientId: "c1", text: "mine", ts: "t" },
      { type: "reset", threadId: null });
    expect(pendingSends(s)).toEqual([]);
  });
});

describe("re-attaching to a run already on screen", () => {
  const started = (): ThreadState =>
    apply(initialThreadState("t"), { type: "runStarted", runId: "r1" });

  const text = (content: string) =>
    ({ type: "runDelta", runId: "r1", event: { kind: "text", content } }) as const;

  // THE BUG: a fresh EventSource sends no Last-Event-ID, so the server replays
  // the turn from offset 0 — and applyStreamEvent appends. A backend restart, an
  // edge 502, or the 5s liveness re-attach therefore rendered the whole turn
  // twice, doubling again on every further re-attach.
  it("rewinds the live view, so the replay rebuilds it instead of doubling it", () => {
    const before = apply(started(), text("Hello "), text("world"));
    expect(before.activeRun?.segments).toEqual([{ t: "text", text: "Hello world" }]);

    const after = apply(before,
      { type: "runReattached", runId: "r1" },
      text("Hello "), text("world"));
    expect(after.activeRun?.segments).toEqual([{ t: "text", text: "Hello world" }]);
  });

  it("leaves a different run alone", () => {
    const s = apply(started(), text("keep me"), { type: "runReattached", runId: "other" });
    expect(s.activeRun?.segments).toEqual([{ t: "text", text: "keep me" }]);
  });

  it("does nothing when there is no run to rewind", () => {
    const s = initialThreadState("t");
    expect(threadReducer(s, { type: "runReattached", runId: "r1" })).toBe(s);
  });

  it("keeps everything about the run except what the stream will resend", () => {
    const s = apply(started(), text("partial"));
    const before = s.activeRun!;
    const after = threadReducer(s, { type: "runReattached", runId: "r1" }).activeRun!;
    expect(after.runId).toBe(before.runId);
    expect(after.startedMs).toBe(before.startedMs);
    expect(after.segments).toEqual([]);
  });

  it("refuses a snapshot older than the rewind, which would restore what it dropped", () => {
    // A poll in flight when the rewind happens answers about the live view that
    // no longer exists; taking it and then folding in the replay is the same
    // double-render one step removed.
    const s = apply(started(), text("a lot of streamed text"));
    const requestedAt = Date.now() - 5_000;
    const rewound = threadReducer(s, { type: "runReattached", runId: "r1" });
    const after = threadReducer(rewound, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt,
      activeRun: { run_id: "r1", started_seq: 1, live: { meta: { segments: [{ t: "text", text: "a lot of streamed text" }] } } },
    });
    expect(after.activeRun?.segments).toEqual([]);
  });
});

describe("a mid-run message closes the live block it landed in", () => {
  // The live half of the placement fix. The server seals the turn it drives;
  // this seals what the tab is rendering, on the same trigger — a mid-run
  // message appearing in the log — so a message typed in ANOTHER tab still cuts
  // the block here. Without it, text streamed after the message merges into the
  // segment above it and the message renders below words written after it.
  const start = () =>
    apply(initialThreadState("t"),
      { type: "runStarted", runId: "r1" },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "before" } });

  it("seals the open text, so later text starts a new segment", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeRun: { run_id: "r1" },
        events: [ev(2, { meta: { during_run: "r1", at_unit: 1 } })] },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "after" } });
    expect(s.activeRun?.segments.map((x) => (x.t === "text" ? x.text : x.t)))
      .toEqual(["before", "after"]);
  });

  it("does not cut again when the same event is replayed", () => {
    // A replayed cursor is free everywhere else and must be free here too: a
    // second seal would cut at a point no message ever arrived at.
    const midRun = ev(2, { meta: { during_run: "r1", at_unit: 1 } });
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeRun: { run_id: "r1" }, events: [midRun] },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: "after" } },
      { type: "eventsReceived", lastSeq: 2, activeRun: { run_id: "r1" }, events: [midRun] },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: " more" } });
    expect(s.activeRun?.segments.map((x) => (x.t === "text" ? x.text : x.t)))
      .toEqual(["before", "after more"]);
  });

  it("leaves an uninterrupted turn as one text segment", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeRun: { run_id: "r1" }, events: [ev(2)] },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: " and more" } });
    expect(s.activeRun?.segments).toHaveLength(1);
  });

  it("ignores a message that interrupted a DIFFERENT run", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeRun: { run_id: "r1" },
        events: [ev(2, { meta: { during_run: "r0", at_unit: 1 } })] },
      { type: "runDelta", runId: "r1", event: { kind: "text", content: " and more" } });
    expect(s.activeRun?.segments).toHaveLength(1);
  });
});

// The committed log's identity is load-bearing: useThreadLog memoizes the
// expensive half of the transcript on exactly these three references, so a poll
// that changes nothing must not replace them. If it does, every message in the
// thread is re-derived every 10 seconds and nothing anywhere fails visibly.
describe("eventsReceived does not churn the committed log", () => {
  const seeded = apply(initialThreadState("t"),
    { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeRun: null });

  it("keeps byId/order/idBySeq identical when a poll brings nothing", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [], lastSeq: 2, activeRun: null });
    expect(s.byId).toBe(seeded.byId);
    expect(s.order).toBe(seeded.order);
    expect(s.idBySeq).toBe(seeded.idBySeq);
  });

  it("keeps them identical when a poll replays events already held", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeRun: null });
    expect(s.byId).toBe(seeded.byId);
    expect(s.order).toBe(seeded.order);
  });

  it("copies, and only copies, when something is genuinely new", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [ev(2), ev(3)], lastSeq: 3, activeRun: null });
    expect(s.order).not.toBe(seeded.order);
    expect(seeded.order).toEqual([1, 2]);          // the old array is untouched
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });
});
