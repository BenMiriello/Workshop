import { describe, expect, it } from "vitest";
import {
  committedEvents, initialThreadState, pendingSends, snapshotThread, threadReducer,
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
      { type: "eventsReceived", events: [ev(3), ev(1)], lastSeq: 3, activeTurn: null },
      { type: "eventsReceived", events: [ev(2)], lastSeq: 3, activeTurn: null });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("dedupes by event id, so a replayed cursor is free", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeTurn: null },
      { type: "eventsReceived", events: [ev(1), ev(2), ev(3)], lastSeq: 3, activeTurn: null });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("advances the cursor and never walks it backwards", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(9)], lastSeq: 9, activeTurn: null },
      { type: "eventsReceived", events: [], lastSeq: 4, activeTurn: null });
    expect(s.cursor).toBe(9);
  });
});

// Syncs overlap by design and resolve out of order (see `syncedAt`). The turn
// was already guarded; the rest of the wholesale-replaced snapshot was not.
describe("out-of-order syncs", () => {
  const queued = (clientId: string) => [{ client_id: clientId, text: clientId, ts: "t" }];

  it("a late response does not restore a queue a newer one already emptied", () => {
    // Unsend a queued message: the write empties the strip, then a poll that
    // was already in flight lands with the pre-write queue. It must not win.
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null,
        queued: queued("q1"), requestedAt: 2000 },
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null,
        queued: [], requestedAt: 3000 },
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null,
        queued: queued("q1"), requestedAt: 1000 });
    expect(s.queued).toEqual([]);
  });

  it("still merges the events a late response carried", () => {
    // The log is append-only and deduped by id, so an older page's events are
    // still true. Only the fields that REPLACE are held back.
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(2)], lastSeq: 2, activeTurn: null,
        queued: [], requestedAt: 3000 },
      { type: "eventsReceived", events: [ev(1)], lastSeq: 1, activeTurn: null,
        queued: queued("q1"), requestedAt: 1000 });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2]);
    expect(s.queued).toEqual([]);
  });

  it("applies the first response, whose predecessor is the zero timestamp", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null,
        queued: queued("q1"), requestedAt: 1000 });
    expect(s.queued).toHaveLength(1);
  });

  it("treats an undated response as current, never as stale", () => {
    // `requestedAt` is optional: a caller that cannot date its response must
    // not be silently ignored forever after one dated sync.
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null,
        queued: queued("q1"), requestedAt: Date.now() },
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null, queued: [] });
    expect(s.queued).toEqual([]);
  });
});

describe("hasMoreOlder", () => {
  it("is set by a fresh (tail) page that says hasMore", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeTurn: null, hasMore: true });
    expect(s.hasMoreOlder).toBe(true);
  });

  it("is left alone by an ordinary cursor sync, which never carries hasMore", () => {
    // The bug this guards: a page fetched with `after=cursor` (every poll once
    // a thread has loaded) always answers `has_more: false` server-side,
    // because it never asked the question. Applying that unconditionally would
    // flip the "load older" affordance off on the very next poll after the
    // initial tail load turned it on.
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeTurn: null, hasMore: true },
      { type: "eventsReceived", events: [ev(21)], lastSeq: 21, activeTurn: null });
    expect(s.hasMoreOlder).toBe(true);
  });

  it("a fresh page can also turn it off, once nothing more is behind it", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeTurn: null, hasMore: true },
      { type: "reset", threadId: "t2" },
      { type: "eventsReceived", events: [ev(5)], lastSeq: 5, activeTurn: null, hasMore: false });
    expect(s.hasMoreOlder).toBe(false);
  });
});

describe("olderEventsReceived", () => {
  it("prepends a page of history in ascending seq order", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20), ev(21)], lastSeq: 21, activeTurn: null },
      { type: "olderEventsReceived", events: [ev(18), ev(19)], hasMore: true });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([18, 19, 20, 21]);
    expect(s.hasMoreOlder).toBe(true);
  });

  it("does not duplicate an event the page already holds", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(19), ev(20)], lastSeq: 20, activeTurn: null },
      { type: "olderEventsReceived", events: [ev(19), ev(18)], hasMore: false });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([18, 19, 20]);
  });

  it("never moves the cursor, so incremental sync is untouched", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeTurn: null },
      { type: "olderEventsReceived", events: [ev(1)], hasMore: false });
    expect(s.cursor).toBe(20);
  });

  it("sets hasMoreOlder false once the server says there is nothing further back", () => {
    const s = apply(initialThreadState("t"),
      { type: "eventsReceived", events: [ev(20)], lastSeq: 20, activeTurn: null, hasMore: true },
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
      { type: "eventsReceived", events: [ev(1, { content: "hello", client_id: "c1" })], lastSeq: 1, activeTurn: null });
    expect(pendingSends(s)).toEqual([]);
  });

  it("keeps a pending send whose text also appears in the log under another id", () => {
    // The exact case content-equality got wrong: the operator said the same
    // thing twice, and the tail match popped the bubble for the wrong one.
    const s = apply(initialThreadState("t"),
      { type: "sendStarted", clientId: "c2", text: "hello", ts: "t" },
      { type: "eventsReceived", events: [ev(1, { content: "hello", client_id: "c1" })], lastSeq: 1, activeTurn: null });
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
  const started = { type: "turnStarted" as const, turnId: "r1" };

  it("folds stream events into the live segments", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "hi " } },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "there" } });
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "hi there" }]);
  });

  it("ignores a delta from a run it is not showing", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "turnDelta", turnId: "other", event: { kind: "text", content: "x" } });
    expect(s.activeTurn?.segments).toEqual([]);
  });

  it("does not let the server's throttled snapshot walk a live turn backwards", () => {
    // The snapshot is written every ~0.4s and deliberately lags the stream.
    // Taking it unconditionally made the transcript flicker on every sync.
    const s = apply(initialThreadState("t"), started,
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "a lot of text" } },
      {
        type: "eventsReceived", events: [], lastSeq: 0,
        activeTurn: { turn_id: "r1", started_seq: 4, live: { meta: { segments: [] } } },
      });
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "a lot of text" }]);
    expect(s.activeTurn?.startedSeq).toBe(4);
  });

  it("takes the snapshot when it is ahead, which is how another tab reattaches", () => {
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeTurn: {
        turn_id: "r1", started_seq: 4,
        live: { meta: { segments: [{ t: "text", text: "from the server" }] } },
      },
    });
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "from the server" }]);
  });

  it("takes a snapshot that grew the SAME segment, not just a longer list", () => {
    // A turn producing one long answer never adds a second segment: the text is
    // appended to the trailing one. Counting segments made a tab that is not
    // holding the socket freeze on the first partial sentence for the whole
    // turn, elapsed clock still ticking.
    const snapshot = (text: string) => ({
      type: "eventsReceived" as const, events: [], lastSeq: 0,
      activeTurn: { turn_id: "r1", started_seq: 4, live: { meta: { segments: [{ t: "text" as const, text }] } } },
    });
    const s = apply(initialThreadState("t"), started,
      snapshot("The first part"), snapshot("The first part, and the rest of it"));
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "The first part, and the rest of it" }]);
  });

  it("still refuses a snapshot whose tail is shorter than what is rendered", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "a long streamed answer" } },
      {
        type: "eventsReceived", events: [], lastSeq: 0,
        activeTurn: { turn_id: "r1", started_seq: 4, live: { meta: { segments: [{ t: "text", text: "a long" }] } } },
      });
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "a long streamed answer" }]);
  });

  it("sees a subagent that grew in place under an unchanged segment count", () => {
    const withSteps = (steps: { t: "text"; text: string }[]) => ({
      type: "eventsReceived" as const, events: [], lastSeq: 0,
      activeTurn: {
        turn_id: "r1", started_seq: 4,
        live: { meta: { segments: [{
          t: "agent" as const, name: "explore", input: "look", id: "a1",
          steps, done: false, result: "",
        }] } },
      },
    });
    const s = apply(initialThreadState("t"), started,
      withSteps([{ t: "text", text: "reading" }]),
      withSteps([{ t: "text", text: "reading" }, { t: "text", text: "found it" }]));
    expect((s.activeTurn?.segments[0] as { steps: unknown[] }).steps).toHaveLength(2);
  });

  it("clears when the server says no run is active", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null });
    expect(s.activeTurn).toBeNull();
  });

  it("ignores a sync answer computed before this tab started the run", () => {
    // useCompaction fires an un-awaited sync() immediately before sending
    // /compact, so a response that predates the run routinely lands after it.
    // Clearing on that answer blanked the live block, made the reducer drop the
    // run's own streamed deltas, and left canDrainQueue true long enough for a
    // queued message to open a rival turn.
    const before = Date.now() - 5_000;
    const s = apply(initialThreadState("t"), started,
      { type: "eventsReceived", events: [], lastSeq: 0, activeTurn: null, requestedAt: before });
    expect(s.activeTurn?.turnId).toBe("r1");
  });

  it("ignores a stale answer that names some OTHER run", () => {
    const before = Date.now() - 5_000;
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt: before,
      activeTurn: { turn_id: "r0", started_seq: 1, live: { meta: { segments: [] } } },
    });
    expect(s.activeTurn?.turnId).toBe("r1");
  });

  it("still clears on an answer asked for after the run started", () => {
    const s = apply(initialThreadState("t"), started, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeTurn: null, requestedAt: Date.now() + 5_000,
    });
    expect(s.activeTurn).toBeNull();
  });

  it("clears on commit, because the reply is in the log now", () => {
    const s = apply(initialThreadState("t"), started, { type: "turnCommitted", turnId: "r1" });
    expect(s.activeTurn).toBeNull();
  });
});

describe("the live context reading", () => {
  const started = { type: "turnStarted" as const, turnId: "r1" };

  it("lands on the active run and dedupes an unchanged value", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "turnContext", turnId: "r1", tokens: 1000 });
    expect(s.activeTurn?.ctxTokens).toBe(1000);
    const same = threadReducer(s, { type: "turnContext", turnId: "r1", tokens: 1000 });
    expect(same).toBe(s); // no state churn on an unchanged reading
    const moved = threadReducer(s, { type: "turnContext", turnId: "r1", tokens: 2500 });
    expect(moved.activeTurn?.ctxTokens).toBe(2500);
  });

  it("ignores a reading for a run it is not showing, and junk values", () => {
    const s0 = apply(initialThreadState("t"), started);
    expect(threadReducer(s0, { type: "turnContext", turnId: "other", tokens: 5 })).toBe(s0);
    expect(threadReducer(s0, { type: "turnContext", turnId: "r1", tokens: 0 })).toBe(s0);
  });

  it("survives the reattach rewind that empties the segments", () => {
    // The replay re-delivers every context event in order anyway, but the
    // reading must not blank in the meantime: the meter renders from it.
    const s = apply(initialThreadState("t"), started,
      { type: "turnContext", turnId: "r1", tokens: 1000 },
      { type: "turnReattached", turnId: "r1" });
    expect(s.activeTurn?.ctxTokens).toBe(1000);
  });
});

describe("reset", () => {
  it("drops everything when switching to another conversation", () => {
    const s = apply(initialThreadState("a"),
      { type: "eventsReceived", events: [ev(1)], lastSeq: 1, activeTurn: null },
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
    apply(initialThreadState("t"), { type: "turnStarted", turnId: "r1" });

  const text = (content: string) =>
    ({ type: "turnDelta", turnId: "r1", event: { kind: "text", content } }) as const;

  // THE BUG: a fresh EventSource sends no Last-Event-ID, so the server replays
  // the turn from offset 0 — and applyStreamEvent appends. A backend restart, an
  // edge 502, or the 5s liveness re-attach therefore rendered the whole turn
  // twice, doubling again on every further re-attach.
  it("rewinds the live view, so the replay rebuilds it instead of doubling it", () => {
    const before = apply(started(), text("Hello "), text("world"));
    expect(before.activeTurn?.segments).toEqual([{ t: "text", text: "Hello world" }]);

    const after = apply(before,
      { type: "turnReattached", turnId: "r1" },
      text("Hello "), text("world"));
    expect(after.activeTurn?.segments).toEqual([{ t: "text", text: "Hello world" }]);
  });

  it("leaves a different run alone", () => {
    const s = apply(started(), text("keep me"), { type: "turnReattached", turnId: "other" });
    expect(s.activeTurn?.segments).toEqual([{ t: "text", text: "keep me" }]);
  });

  it("does nothing when there is no run to rewind", () => {
    const s = initialThreadState("t");
    expect(threadReducer(s, { type: "turnReattached", turnId: "r1" })).toBe(s);
  });

  it("keeps everything about the run except what the stream will resend", () => {
    const s = apply(started(), text("partial"));
    const before = s.activeTurn!;
    const after = threadReducer(s, { type: "turnReattached", turnId: "r1" }).activeTurn!;
    expect(after.turnId).toBe(before.turnId);
    expect(after.startedMs).toBe(before.startedMs);
    expect(after.segments).toEqual([]);
  });

  it("refuses a snapshot older than the rewind, which would restore what it dropped", () => {
    // A poll in flight when the rewind happens answers about the live view that
    // no longer exists; taking it and then folding in the replay is the same
    // double-render one step removed.
    const s = apply(started(), text("a lot of streamed text"));
    const requestedAt = Date.now() - 5_000;
    const rewound = threadReducer(s, { type: "turnReattached", turnId: "r1" });
    const after = threadReducer(rewound, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt,
      activeTurn: { turn_id: "r1", started_seq: 1, live: { meta: { segments: [{ t: "text", text: "a lot of streamed text" }] } } },
    });
    expect(after.activeTurn?.segments).toEqual([]);
  });

  it("refuses a CURRENT snapshot mid-replay, which the stale check does not catch", () => {
    // The doubling of 2026-08-18: the reconnect fires the rewind AND a fresh
    // sync. That sync is not stale (it was issued at the reconnect, after the
    // rewind's re-stamp), so the old stale guard let it through, it restored the
    // full throttled snapshot onto the emptied segments, and the offset-0 replay
    // then appended the same events on top. `streamOwned` closes it: while a
    // socket is (re)attached, the poll never supplies segments.
    const s = apply(started(), text("a lot of streamed text"));
    const rewound = threadReducer(s, { type: "turnReattached", turnId: "r1" });
    const after = threadReducer(rewound, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt: Date.now() + 1000,
      activeTurn: { turn_id: "r1", started_seq: 1,
        live: { meta: { segments: [{ t: "text", text: "a lot of streamed text" }] } } },
    });
    expect(after.activeTurn?.segments).toEqual([]);
    // ...and the replay alone rebuilds it, exactly once.
    const replayed = apply(after, text("a lot of streamed text"));
    expect(replayed.activeTurn?.segments).toEqual([{ t: "text", text: "a lot of streamed text" }]);
  });

  it("hands segments back to the poll once the stream gives up", () => {
    // MAX_RECONNECTS exhausted: no socket is held, so the stream is not
    // replaying and the poll is the only source left. After `turnStreamDropped`
    // the next snapshot must advance the view again.
    const s = apply(started(), text("partial"),
      { type: "turnReattached", turnId: "r1" },   // owns segments, rewound to []
      { type: "turnStreamDropped", turnId: "r1" }); // stream let go
    expect(s.activeTurn?.streamOwned).toBe(false);
    const after = threadReducer(s, {
      type: "eventsReceived", events: [], lastSeq: 0, requestedAt: Date.now() + 1000,
      activeTurn: { turn_id: "r1", started_seq: 1,
        live: { meta: { segments: [{ t: "text", text: "server has more" }] } } },
    });
    expect(after.activeTurn?.segments).toEqual([{ t: "text", text: "server has more" }]);
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
      { type: "turnStarted", turnId: "r1" },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "before" } });

  it("seals the open text, so later text starts a new segment", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeTurn: { turn_id: "r1" },
        events: [ev(2, { meta: { during_turn: "r1", at_unit: 1 } })] },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "after" } });
    expect(s.activeTurn?.segments.map((x) => (x.t === "text" ? x.text : x.t)))
      .toEqual(["before", "after"]);
  });

  it("does not cut again when the same event is replayed", () => {
    // A replayed cursor is free everywhere else and must be free here too: a
    // second seal would cut at a point no message ever arrived at.
    const midRun = ev(2, { meta: { during_turn: "r1", at_unit: 1 } });
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeTurn: { turn_id: "r1" }, events: [midRun] },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "after" } },
      { type: "eventsReceived", lastSeq: 2, activeTurn: { turn_id: "r1" }, events: [midRun] },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: " more" } });
    expect(s.activeTurn?.segments.map((x) => (x.t === "text" ? x.text : x.t)))
      .toEqual(["before", "after more"]);
  });

  it("leaves an uninterrupted turn as one text segment", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeTurn: { turn_id: "r1" }, events: [ev(2)] },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: " and more" } });
    expect(s.activeTurn?.segments).toHaveLength(1);
  });

  it("ignores a message that interrupted a DIFFERENT run", () => {
    const s = apply(start(),
      { type: "eventsReceived", lastSeq: 2, activeTurn: { turn_id: "r1" },
        events: [ev(2, { meta: { during_turn: "r0", at_unit: 1 } })] },
      { type: "turnDelta", turnId: "r1", event: { kind: "text", content: " and more" } });
    expect(s.activeTurn?.segments).toHaveLength(1);
  });
});

// The committed log's identity is load-bearing: useThreadLog memoizes the
// expensive half of the transcript on exactly these three references, so a poll
// that changes nothing must not replace them. If it does, every message in the
// thread is re-derived every 10 seconds and nothing anywhere fails visibly.
describe("eventsReceived does not churn the committed log", () => {
  const seeded = apply(initialThreadState("t"),
    { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeTurn: null });

  it("keeps byId/order/idBySeq identical when a poll brings nothing", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [], lastSeq: 2, activeTurn: null });
    expect(s.byId).toBe(seeded.byId);
    expect(s.order).toBe(seeded.order);
    expect(s.idBySeq).toBe(seeded.idBySeq);
  });

  it("keeps them identical when a poll replays events already held", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeTurn: null });
    expect(s.byId).toBe(seeded.byId);
    expect(s.order).toBe(seeded.order);
  });

  it("copies, and only copies, when something is genuinely new", () => {
    const s = threadReducer(seeded,
      { type: "eventsReceived", events: [ev(2), ev(3)], lastSeq: 3, activeTurn: null });
    expect(s.order).not.toBe(seeded.order);
    expect(seeded.order).toEqual([1, 2]);          // the old array is untouched
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
  });
});

// `seedThread` is how threadCache.ts reseeds a revisited thread instantly —
// see useThreadLog.ts's STALE-WHILE-REVALIDATE comment. These pin the
// invariants that make a cache a safe second owner of a copy of the state
// rather than a new source of the reordering/wrong-thread bugs ADR 0018 and
// this store's own header exist to remove.
describe("seedThread", () => {
  it("adopts the snapshot's committed log and cursor", () => {
    const loaded = apply(initialThreadState("t1"),
      { type: "eventsReceived", events: [ev(1), ev(2), ev(3)], lastSeq: 3, activeTurn: null });
    const snapshot = snapshotThread(loaded);
    const s = threadReducer(initialThreadState(null),
      { type: "seedThread", threadId: "t1", snapshot });
    expect(s.threadId).toBe("t1");
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(s.cursor).toBe(3);
  });

  it("never adopts a snapshot recorded under a different thread's id", () => {
    // Belt-and-braces on top of the cache already being keyed by threadId
    // (threadCache.ts): even handed a mismatched pair directly, the reducer
    // must not paint thread B's messages under thread A's id.
    const snapshotForB = snapshotThread(apply(initialThreadState("B"),
      { type: "eventsReceived", events: [ev(9)], lastSeq: 9, activeTurn: null }));
    const s = threadReducer(initialThreadState(null),
      { type: "seedThread", threadId: "A", snapshot: snapshotForB });
    expect(s.threadId).toBe("A");
    expect(committedEvents(s)).toEqual([]);
  });

  it("never carries pending sends or the clock, from the snapshot or from what it replaces", () => {
    // Pending sends have exactly one owner (useSendOutbox, restoring per
    // thread from durable storage) and a cache seed must not become a second
    // one — see the `seedThread` action's own comment in threadStore.ts.
    const withPending = apply(initialThreadState("t1"),
      { type: "sendStarted", clientId: "c1", text: "hi", ts: "t" });
    expect(pendingSends(withPending)).toHaveLength(1);
    const snapshot = snapshotThread(
      apply(initialThreadState("t1"),
        { type: "eventsReceived", events: [ev(1)], lastSeq: 1, activeTurn: null }));
    const s = threadReducer(withPending, { type: "seedThread", threadId: "t1", snapshot });
    expect(pendingSends(s)).toEqual([]);
    expect(s.clock).toBe(0);
  });

  it("never carries a run in flight: a cached activeTurn is a claim about the past", () => {
    // The working indicator, the elapsed clock, and the EventSource Chat.tsx
    // opens against `activeTurn.turnId` are all driven by this field. A cached
    // one paints a turn that has usually already ended, and the attach it
    // triggers re-stamps `localStartedAt` to now -- which makes `adoptTurn`
    // read the catch-up sync's own `active_turn: null` as STALE and keep the
    // phantom. The only honest answer between a seed and its catch-up is none.
    const running = apply(initialThreadState("t1"), {
      type: "eventsReceived",
      events: [ev(1)],
      lastSeq: 1,
      activeTurn: { turn_id: "run-1", started_seq: 1, live: { meta: { segments: [] } } },
    });
    expect(running.activeTurn).not.toBeNull();
    const s = threadReducer(initialThreadState(null), {
      type: "seedThread", threadId: "t1", snapshot: snapshotThread(running),
    });
    expect(committedEvents(s).map((e) => e.seq)).toEqual([1]);
    expect(s.activeTurn).toBeNull();
  });

  it("a branch change drops a seeded snapshot outright rather than merging it", () => {
    // Mirrors what `sync()` does on a real branch change: `reset`, then
    // re-read from zero. A seeded thread must be exactly as droppable as a
    // live one -- nothing about having come from the cache may make it stick
    // around after the branch it belongs to is gone.
    const snapshot = snapshotThread(apply(initialThreadState("t1"),
      { type: "eventsReceived", events: [ev(1), ev(2)], lastSeq: 2, activeTurn: null }));
    const seeded = threadReducer(initialThreadState(null),
      { type: "seedThread", threadId: "t1", snapshot });
    expect(committedEvents(seeded)).toHaveLength(2);
    const afterBranchChange = threadReducer(seeded, { type: "reset", threadId: "t1" });
    expect(committedEvents(afterBranchChange)).toEqual([]);
    expect(afterBranchChange.cursor).toBe(0);
  });

  it("a seeded thread plus one cursor sync matches a cold full load exactly", () => {
    // The whole point of the cache: revalidating a seed with ONE small
    // `after=<cursor>` page must land on the identical state a cold
    // `after=0` tail read would have produced, not an approximation of it.
    const allEvents = [ev(1), ev(2), ev(3), ev(4), ev(5)];

    // Cold path: everything in one read, as a fresh thread open does today.
    const cold = threadReducer(initialThreadState("t1"),
      { type: "eventsReceived", events: allEvents, lastSeq: 5, activeTurn: null, hasMore: false });

    // Cached path: this tab saw events 1-3 on an earlier visit (captured as a
    // snapshot), left, and comes back to find 4-5 have since landed. Seeding
    // plus one cursor-bounded sync should catch it up to the same place.
    const firstVisit = threadReducer(initialThreadState("t1"),
      { type: "eventsReceived", events: allEvents.slice(0, 3), lastSeq: 3, activeTurn: null });
    const snapshot = snapshotThread(firstVisit);
    const reseeded = threadReducer(initialThreadState(null),
      { type: "seedThread", threadId: "t1", snapshot });
    expect(reseeded.cursor).toBe(3);
    const caughtUp = threadReducer(reseeded,
      { type: "eventsReceived", events: allEvents.slice(3), lastSeq: 5, activeTurn: null });

    expect(committedEvents(caughtUp)).toEqual(committedEvents(cold));
    expect(caughtUp.cursor).toBe(cold.cursor);
    expect(caughtUp.order).toEqual(cold.order);
  });
});
