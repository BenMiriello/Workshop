import { describe, expect, it } from "vitest";
import {
  armLevelGloss, armLevelName, ceilingLine, makerPrimary, makerStatus,
  type MakerSummary,
} from "./makers";

// A maker that is armed, resident and holding nothing. Each test overrides the
// one or two fields it is about, so a new field on the API cannot silently
// change what these assert.
const base: MakerSummary = {
  scope: "project:atlas",
  dir: "/srv/projects/atlas",
  arm_level: "supervised",
  armed: true,
  ceiling: "in_review",
  global_override_engaged: false,
  runner_resident: true,
  activity: "present",
  activity_detail: "a worker is resident, but nothing it has written claims a ticket",
  ticket_id: null,
  ticket_title: null,
  ticket_status: null,
};

const working: MakerSummary = {
  ...base,
  activity: "working",
  activity_detail: "a live session in this scope holds a ticket",
  ticket_id: "t-91ac",
  ticket_title: "Fix the runner picker",
  ticket_status: "in_progress",
};

describe("makerStatus", () => {
  it("is running while a session holds a ticket", () => {
    expect(makerStatus(working)).toEqual({ kind: "running", label: "Working" });
  });

  it("is running even mid-stop, because a worker holding a ticket is working", () => {
    // stop_work disarms and then signals; for that window the scope is reactive
    // while the session is still on a ticket. Liveness is the true answer.
    expect(makerStatus({ ...working, armed: false, arm_level: "reactive" }).kind).toBe("running");
  });

  it("is WARN, never ok, when armed with no worker resident", () => {
    // The whole feature exists to make this state visible: armed, so the
    // operator believes tickets are moving, and nothing on the tree to move
    // them. A green ok here is the bug.
    const s = makerStatus({ ...base, activity: "absent", runner_resident: false });
    expect(s).toEqual({ kind: "warn", label: "No worker" });
  });

  it("is warn when the global brake supersedes an armed scope", () => {
    expect(makerStatus({ ...base, global_override_engaged: true }))
      .toEqual({ kind: "warn", label: "Brake on" });
  });

  it("prefers the brake to the missing worker, since the brake is why", () => {
    const s = makerStatus({
      ...base, global_override_engaged: true, runner_resident: false, activity: "absent",
    });
    expect(s.label).toBe("Brake on");
  });

  it("is stopped when disarmed and the tree is clear", () => {
    expect(makerStatus({
      ...base, armed: false, arm_level: "reactive", runner_resident: false, activity: "absent",
    })).toEqual({ kind: "stopped", label: "Off" });
  });

  it("warns when disarmed while a worker is still on the tree", () => {
    expect(makerStatus({ ...base, armed: false, arm_level: "reactive" }))
      .toEqual({ kind: "warn", label: "Off, worker still resident" });
  });

  it("is ok only when armed, resident, unbraked and holding nothing", () => {
    expect(makerStatus(base)).toEqual({ kind: "ok", label: "Ready" });
  });
});

describe("makerPrimary", () => {
  it("leads with the ticket a working maker holds", () => {
    expect(makerPrimary(working)).toEqual({
      text: "Working on",
      ticketId: "t-91ac",
      ticketLabel: "Fix the runner picker",
      ticketStatus: "in_progress",
    });
  });

  it("falls back to the ticket id when the session carried no title", () => {
    const p = makerPrimary({ ...working, ticket_title: null });
    expect(p.ticketLabel).toBe("t-91ac");
    expect(p.ticketId).toBe("t-91ac");
  });

  it("says so rather than linking nowhere when working names no ticket", () => {
    const p = makerPrimary({ ...working, ticket_id: null, ticket_title: null });
    expect(p.ticketId).toBeNull();
    expect(p.text).toContain("did not name a ticket");
  });

  it("never claims a present worker is idle, because the channel cannot tell", () => {
    // `present` covers idling on the poll AND sweeping without a claim. The
    // line may say only what both have in common: here, holding nothing.
    const p = makerPrimary(base);
    expect(p.text).toBe("A worker is here with no ticket claimed.");
    expect(p.text).not.toMatch(/idle|idling|sweep/i);
    expect(p.ticketId).toBeNull();
  });

  it("names the missing worker when armed and absent", () => {
    expect(makerPrimary({ ...base, activity: "absent", runner_resident: false }).text)
      .toBe("Armed, but no worker is on this tree.");
  });

  it("names the brake as the reason when it is engaged", () => {
    expect(makerPrimary({ ...base, global_override_engaged: true }).text)
      .toBe("A worker is here, but the global brake is on.");
    expect(makerPrimary({
      ...base, global_override_engaged: true, activity: "absent", runner_resident: false,
    }).text).toBe("Armed, but the global brake is on.");
  });

  it("says where the tickets go when the maker is off", () => {
    expect(makerPrimary({
      ...base, armed: false, arm_level: "reactive", activity: "absent", runner_resident: false,
    }).text).toBe("Filed tickets wait in the queue.");
  });

  it("does not report a clear tree when a disarmed scope still has a worker", () => {
    expect(makerPrimary({ ...base, armed: false, arm_level: "reactive" }).text)
      .toBe("A worker is still on this tree.");
  });
});

describe("arm level copy", () => {
  it("names the three levels the way the rest of the app does", () => {
    expect(armLevelName("reactive")).toBe("Off");
    expect(armLevelName("supervised")).toBe("Run to review");
    expect(armLevelName("autonomous")).toBe("Run to done");
  });

  it("glosses each level with what it does to filed tickets", () => {
    expect(armLevelGloss("reactive")).toContain("wait for you");
    expect(armLevelGloss("supervised")).toContain("review");
    expect(armLevelGloss("autonomous")).toContain("done");
  });
});

describe("ceilingLine", () => {
  it("reads the ceiling only when the scope is armed", () => {
    expect(ceilingLine({ armed: true, ceiling: "in_review" })).toContain("stops at review");
    expect(ceilingLine({ armed: true, ceiling: "done" })).toContain("through to done");
  });

  it("says nothing runs at all when disarmed, rather than 'no limit'", () => {
    expect(ceilingLine({ armed: false, ceiling: null })).toContain("Nothing runs unattended");
  });

  it("does not invent a ceiling for an armed scope that reports none", () => {
    expect(ceilingLine({ armed: true, ceiling: null })).toBe("No stage ceiling is set for this scope.");
  });
});
