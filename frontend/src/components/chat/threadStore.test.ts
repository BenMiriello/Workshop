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

  it("clears when the server says no run is active", () => {
    const s = apply(initialThreadState("t"), started,
      { type: "eventsReceived", events: [], lastSeq: 0, activeRun: null });
    expect(s.activeRun).toBeNull();
  });

  it("clears on commit, because the reply is in the log now", () => {
    const s = apply(initialThreadState("t"), started, { type: "runCommitted", runId: "r1" });
    expect(s.activeRun).toBeNull();
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
