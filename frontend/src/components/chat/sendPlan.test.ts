import { describe, expect, it } from "vitest";
import { canDrainQueue, planSend } from "./sendPlan";
import { TURN_LABEL, elapsedSeconds, turnPhase } from "./turnStatus";

const idle = { compacting: false, starting: false, runActive: false };

describe("planSend", () => {
  it("sends as a new turn when nothing is running", () => {
    expect(planSend("hello", idle)).toBe("newTurn");
  });

  it("hands a message to the run in flight", () => {
    expect(planSend("hello", { ...idle, runActive: true })).toBe("inject");
  });

  it("treats a turn that has been asked for as running", () => {
    // The window the 250ms drain timer used to cover: the POST is out and the
    // server has not answered, so nothing observable says a turn is coming.
    expect(planSend("hello", { ...idle, starting: true })).toBe("inject");
  });

  it("queues a slash command rather than injecting it", () => {
    // The CLI's command layer only runs between turns; a command injected
    // mid-turn is swallowed silently.
    expect(planSend("/compact", { ...idle, runActive: true })).toBe("queue");
    expect(planSend("/cost extra", { ...idle, starting: true })).toBe("queue");
  });

  it("sends a slash command straight out when nothing is running", () => {
    expect(planSend("/compact", idle)).toBe("newTurn");
  });

  it("does not mistake a path for a command", () => {
    expect(planSend("/usr/bin/foo is missing", { ...idle, runActive: true })).toBe("inject");
  });

  it("queues everything while a fold is in flight", () => {
    // Including a command: the /compact already asked for has to keep the
    // position the operator gave it.
    expect(planSend("hello", { ...idle, compacting: true })).toBe("queue");
    expect(planSend("/clear", { ...idle, compacting: true })).toBe("queue");
  });
});

describe("canDrainQueue", () => {
  it("drains when the chat is genuinely idle", () => {
    expect(canDrainQueue({ ...idle, loadingThread: false })).toBe(true);
  });

  it("holds while a turn is starting, running, folding, or loading", () => {
    expect(canDrainQueue({ ...idle, loadingThread: false, starting: true })).toBe(false);
    expect(canDrainQueue({ ...idle, loadingThread: false, runActive: true })).toBe(false);
    expect(canDrainQueue({ ...idle, loadingThread: false, compacting: true })).toBe(false);
    expect(canDrainQueue({ ...idle, loadingThread: true })).toBe(false);
  });

  it("does not fire the queue at the moment the operator stops a turn", () => {
    // The trap: Stop ends the run, which makes the chat idle, which is exactly
    // the condition the queue self-drains on. So the press that was meant to
    // take the wheel back would instead send everything typed ahead, unattended.
    expect(canDrainQueue({ ...idle, loadingThread: false, heldByStop: true })).toBe(false);
  });

  it("resumes once the operator sends again", () => {
    // The hold is released on the next send (Chat.tsx, the pipeline's onSend),
    // so nothing queued is stranded — it goes out when they are driving again.
    expect(canDrainQueue({ ...idle, loadingThread: false, heldByStop: false })).toBe(true);
  });

  it("is absent by default, so every other caller is unaffected", () => {
    expect(canDrainQueue({ ...idle, loadingThread: false })).toBe(true);
  });
});

describe("the working indicator never goes quiet on a running turn", () => {
  const base = { attached: true, reconnecting: false, compacting: false };

  it("says what a plain turn is doing", () => {
    expect(TURN_LABEL[turnPhase(base)]).toBe("Working");
  });

  it("names a compaction rather than calling it work", () => {
    expect(TURN_LABEL[turnPhase({ ...base, compacting: true })]).toBe("Compacting");
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
    expect(turnPhase({ attached: false, reconnecting: true, compacting: false })).toBe("reconnecting");
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
