import { describe, expect, it } from "vitest";
import { jobIsLive, jobStatusKind } from "./jobs";

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
