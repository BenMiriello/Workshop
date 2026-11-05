import { describe, expect, it } from "vitest";
import type { LogEvent } from "../chat";
import { cancelStep, readChatState, type CancelSnapshot } from "./chatTargetsPlan";

const base: CancelSnapshot = {
  answered: true, sendOk: true, queued: false,
  message: { id: "m1", seq: 10 }, firstMessage: false, activeTurn: null, turnEndedSinceMessage: false, busy: null,
};

describe("cancelStep, in the doc's order", () => {
  it("1. waits while the send has not answered", () => {
    expect(cancelStep({ ...base, answered: false, sendOk: false })).toEqual({ kind: "wait" });
  });
  it("2. a failed send was not sent", () => {
    expect(cancelStep({ ...base, sendOk: false, queued: true })).toEqual({ kind: "notSent" });
  });
  it("3. a queued message comes off the queue", () => {
    expect(cancelStep({ ...base, queued: true, message: null })).toEqual({ kind: "unqueue" });
  });
  it("4. its running turn is stopped first", () => {
    expect(cancelStep({ ...base, activeTurn: { turnId: "t1", startedSeq: 11 } }))
      .toEqual({ kind: "stop", turnId: "t1" });
  });
  it("5. a finished turn goes back to before the message", () => {
    expect(cancelStep({ ...base, turnEndedSinceMessage: true }))
      .toEqual({ kind: "goBack", anchor: "m1" });
  });
  it("6. another turn running since is refused", () => {
    expect(cancelStep({
      ...base, turnEndedSinceMessage: true, activeTurn: { turnId: "t2", startedSeq: 14 },
    })).toEqual({ kind: "refuse", reason: "Another turn started." });
  });
  it("6. an operation holding the chat is refused", () => {
    expect(cancelStep({ ...base, busy: "compact" }).kind).toBe("refuse");
  });
  it("a chat's first message unsends the chat, stopping its turn when running", () => {
    expect(cancelStep({ ...base, firstMessage: true, activeTurn: { turnId: "t1", startedSeq: 11 } }))
      .toEqual({ kind: "unsendChat", turnId: "t1" });
    expect(cancelStep({ ...base, firstMessage: true, turnEndedSinceMessage: true }))
      .toEqual({ kind: "unsendChat", turnId: null });
  });
  it("a queued first message is still just taken off the queue", () => {
    expect(cancelStep({ ...base, firstMessage: true, queued: true })).toEqual({ kind: "unqueue" });
  });
  it("refuses when the message cannot be found", () => {
    expect(cancelStep({ ...base, message: null }).kind).toBe("refuse");
  });
});

const ev = (e: Partial<LogEvent> & { seq: number; kind: LogEvent["kind"] }): LogEvent => ({
  id: `e${e.seq}`, ts: "", ...e,
});

describe("readChatState", () => {
  it("is not sure a message is the first when older events exist", () => {
    const s = readChatState({
      events: [ev({ seq: 40, kind: "message", role: "user", client_id: "c1" })],
      queued: [], active_turn: null, pending_operation: null, has_more: true,
    }, "c1");
    expect(s.firstMessage).toBe(false);
  });

  const page = (events: LogEvent[], extra: object = {}) => ({
    events, queued: [], active_turn: null, pending_operation: null, has_more: false, ...extra,
  });

  it("finds the message by client id and a turn that ended after it", () => {
    const s = readChatState(page([
      ev({ seq: 9, kind: "message", role: "user", client_id: "other" }),
      ev({ seq: 10, kind: "message", role: "user", client_id: "c1" }),
      ev({ seq: 11, kind: "turn", state: "started", turn_id: "t1" }),
      ev({ seq: 12, kind: "turn", state: "finished", turn_id: "t1" }),
    ]), "c1");
    expect(s.message).toEqual({ id: "e10", seq: 10 });
    expect(s.firstMessage).toBe(false);
    expect(s.turnEndedSinceMessage).toBe(true);
    expect(s.queued).toBe(false);
  });

  it("does not count a turn that ended before the message", () => {
    const s = readChatState(page([
      ev({ seq: 8, kind: "turn", state: "finished", turn_id: "t0" }),
      ev({ seq: 10, kind: "message", role: "user", client_id: "c1" }),
    ], { active_turn: { turn_id: "t1", started_seq: 11 } }), "c1");
    expect(s.turnEndedSinceMessage).toBe(false);
    expect(s.firstMessage).toBe(true);
    expect(s.activeTurn).toEqual({ turnId: "t1", startedSeq: 11 });
  });

  it("sees a queued message by client id", () => {
    const s = readChatState(page([], {
      queued: [{ client_id: "c1", text: "x", ts: "", received_at: "" }],
      pending_operation: "compact",
    }), "c1");
    expect(s.queued).toBe(true);
    expect(s.message).toBeNull();
    expect(s.busy).toBe("compact");
  });
});
