import { describe, expect, it } from "vitest";
import { jobIsLive, jobStatusKind, sweepCounts } from "./jobs";

describe("jobStatusKind", () => {
  it("maps every known status onto one of the five design kinds", () => {
    expect(jobStatusKind("running")).toBe("running");
    expect(jobStatusKind("done")).toBe("ok");
    expect(jobStatusKind("queued")).toBe("stopped");
    expect(jobStatusKind("cancelled")).toBe("stopped");
    expect(jobStatusKind("failed")).toBe("danger");
  });

  it("gives awaiting_input its own amber, everywhere", () => {
    // TicketDetail's run list inlined its own ternary with no awaiting_input
    // case, so the same status rendered grey there and amber elsewhere.
    expect(jobStatusKind("awaiting_input")).toBe("warn");
  });

  it("falls back to stopped for an unknown or missing status", () => {
    expect(jobStatusKind("wat")).toBe("stopped");
    expect(jobStatusKind(undefined)).toBe("stopped");
  });
});

describe("jobIsLive", () => {
  it("is true only while the run can still move on its own", () => {
    expect(jobIsLive("running")).toBe(true);
    expect(jobIsLive("queued")).toBe(true);
  });

  it("treats awaiting_input as terminal, because only the operator moves it", () => {
    expect(jobIsLive("awaiting_input")).toBe(false);
  });

  it("stops polling on every end state", () => {
    for (const s of ["done", "failed", "cancelled", "wat", undefined]) {
      expect(jobIsLive(s)).toBe(false);
    }
  });
});

describe("sweepCounts", () => {
  it("says what a sweep worked", () => {
    expect(sweepCounts({ tasks_done: 3, tasks_failed: 0 })).toBe("3 done");
    expect(sweepCounts({ tasks_done: 3, tasks_failed: 1 })).toBe("3 done, 1 failed");
    expect(sweepCounts({ tasks_done: 0, tasks_failed: 2 })).toBe("2 failed");
  });

  it("says nothing when a sweep worked nothing", () => {
    // 66 of the 100 most recent runs are `0 done, 0 failed` (measured
    // 2026-09-01). Printing that would put the same four characters on two
    // thirds of every runs list, which labels nothing.
    expect(sweepCounts({ tasks_done: 0, tasks_failed: 0 })).toBeNull();
    expect(sweepCounts({ tasks_done: null, tasks_failed: null })).toBeNull();
    expect(sweepCounts({})).toBeNull();
  });
});
