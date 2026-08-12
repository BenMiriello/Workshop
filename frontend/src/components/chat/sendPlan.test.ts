import { describe, expect, it } from "vitest";
import { planSend } from "./sendPlan";
import { TURN_LABEL, elapsedSeconds, turnPhase } from "./turnStatus";

const idle = { compacting: false, starting: false, runActive: false, hasThread: true };

describe("planSend", () => {
  // THE PLAN NEVER SAYS "DO NOT SEND THIS YET" ANY MORE, and that is the fix.
  //
  // It used to answer "queue" for a message typed during a turn that reads
  // nothing (a slash command, a voice turn) and for a command typed during any
  // turn. Both facts are still true, and both are now the SERVER's to act on:
  // the message is POSTed, the server records it on the thread's queue, and the
  // server sends it when the run ends. When the browser held those messages
  // instead, delivery depended on that tab being open on that thread at the
  // moment the turn ended — and on 2026-08-12 it was not, on any device, and
  // two of the operator's messages were lost with no trace on the server.
  it("sends as a new turn when nothing is running", () => {
    expect(planSend("hello", idle)).toBe("newTurn");
  });

  it("addresses the chat while a turn that reads nothing is running", () => {
    // 2026-08-10 and again 2026-08-12: `/compact` opens a turn that never reads
    // a word of input. The message still goes to the server NOW; the server is
    // what decides it has to wait, and it is what sends it afterwards.
    expect(planSend("and also do this", { ...idle, runActive: true })).toBe("inject");
  });

  it("sends a slash command to the server rather than holding it", () => {
    // The CLI's command layer only runs between turns, so this cannot be
    // injected into the running turn. The server queues it as its own turn.
    expect(planSend("/compact", { ...idle, runActive: true })).toBe("inject");
    expect(planSend("/cost extra", { ...idle, starting: true })).toBe("inject");
  });

  it("sends a slash command straight out when nothing is running", () => {
    expect(planSend("/compact", idle)).toBe("newTurn");
  });

  it("treats a turn that has been asked for as running", () => {
    // The window the 250ms drain timer used to cover: the POST is out and the
    // server has not answered, so nothing observable says a turn is coming.
    expect(planSend("hello", { ...idle, starting: true })).toBe("inject");
  });

  it("sends during a fold too", () => {
    // A fold is a server-side operation on this thread, and the endpoint that
    // receives the message is the one that knows where the boundary ended up.
    expect(planSend("hello", { ...idle, compacting: true })).toBe("inject");
    expect(planSend("/clear", { ...idle, compacting: true })).toBe("inject");
  });

  it("starts the thread with the first message of a blank chat", () => {
    expect(planSend("hello", { ...idle, hasThread: false })).toBe("newTurn");
  });

  it("holds ONLY a second message racing the request that creates the chat", () => {
    // The one thing the client still serializes, because it is the one thing
    // the server cannot: two POSTs against a chat with no id yet would make two
    // chats. It waits for the id and goes immediately after — and it is durable
    // while it waits (useSendOutbox), not merely remembered.
    expect(planSend("and this", { ...idle, hasThread: false, starting: true }))
      .toBe("awaitThread");
  });
});

describe("the working indicator never goes quiet on a running turn", () => {
  const base = { attached: true, reconnecting: false };

  it("says what a plain turn is doing", () => {
    expect(TURN_LABEL[turnPhase(base)]).toBe("Working");
  });

  it("says so while the socket is being retried", () => {
    expect(TURN_LABEL[turnPhase({ ...base, reconnecting: true })]).toBe("Reconnecting");
  });

  it("still reports a run this tab is not streaming", () => {
    // The fifty-minute silence: out of reconnect budget, or stopped watching, or
    // started in another tab. The run is in flight either way.
    expect(TURN_LABEL[turnPhase({ ...base, attached: false })]).toBe("Still running");
  });

  it("prefers reconnecting over detached, because it is the more specific fact", () => {
    expect(turnPhase({ attached: false, reconnecting: true })).toBe("reconnecting");
  });
});

describe("elapsedSeconds", () => {
  it("counts from the run's real start, so a reload does not restart the clock", () => {
    expect(elapsedSeconds(1_000_000, 1_090_000)).toBe(90);
  });

  it("is zero when there is no anchor to count from", () => {
    expect(elapsedSeconds(null, 1_000)).toBe(0);
    expect(elapsedSeconds(NaN, 1_000)).toBe(0);
  });

  it("never counts backwards off a clock skew", () => {
    expect(elapsedSeconds(2_000, 1_000)).toBe(0);
  });
});
