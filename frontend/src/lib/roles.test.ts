import { describe, expect, it } from "vitest";
import {
  ARMED_VALUES, WRITE_AUTHORITIES, armedGloss, armedName, armedStatus, armingBody,
  armingSummary, asArmed, asWriteAuthority, assignedThreads, isArmed,
  roleUsage, rolesFromCatalog, writesGloss, type ArmableThread,
} from "./roles";

// ArmableThread IS the wire's ThreadSummary now, so the fixture has to supply
// every field the route always answers with. That is the point: a builder that
// could omit `context_key` was describing a record the backend never sends.
const thread = (over: Partial<ArmableThread> & { id: string }): ArmableThread => ({
  title: "a chat",
  scope: "workshop",
  context_key: "workshop",
  last_message: "",
  message_count: 0,
  mode: "chat",
  running: false,
  updated_at: "2026-08-29T12:00:00Z",
  armed: "off",
  role: null,
  ...over,
});

describe("armed", () => {
  it("is the four values the backend enum carries, off first", () => {
    expect([...ARMED_VALUES]).toEqual(["off", "on-message", "schedule", "on-ready-ticket"]);
  });

  it("clamps anything it does not recognise to off", () => {
    // The failure direction is not symmetric: a thread whose arming cannot be
    // read must not run unasked.
    for (const bad of [undefined, null, "", "yes", "true", 1, {}, "ON-MESSAGE"]) {
      expect(asArmed(bad)).toBe("off");
      expect(isArmed(bad)).toBe(false);
    }
  });

  it("keeps every legal value", () => {
    for (const v of ARMED_VALUES) expect(asArmed(v)).toBe(v);
  });

  it("counts every value but off as armed", () => {
    expect(isArmed("off")).toBe(false);
    expect(isArmed("on-message")).toBe(true);
    expect(isArmed("schedule")).toBe(true);
    expect(isArmed("on-ready-ticket")).toBe(true);
  });

  it("marks the exception, not the default", () => {
    expect(armedStatus("off")).toBe("stopped");
    expect(armedStatus("schedule")).toBe("ok");
    // Never `running`: whether a turn is in flight is a different question and
    // a different field on the row.
    for (const v of ARMED_VALUES) expect(armedStatus(v)).not.toBe("running");
  });

  it("names and glosses every value, with no two the same", () => {
    const names = ARMED_VALUES.map(armedName);
    const glosses = ARMED_VALUES.map(armedGloss);
    expect(new Set(names).size).toBe(ARMED_VALUES.length);
    expect(new Set(glosses).size).toBe(ARMED_VALUES.length);
    expect(names.every((n) => n.length > 0)).toBe(true);
    expect(glosses.every((g) => g.length > 0)).toBe(true);
  });
});

describe("write authority", () => {
  it("stays the closed five", () => {
    expect([...WRITE_AUTHORITIES]).toEqual(
      ["maker", "reviewer", "triager", "keeper", "broker"]);
  });

  it("rejects anything outside it, including a free-text role name", () => {
    for (const bad of [null, undefined, "", "Planner", "builder", "assistant"]) {
      expect(asWriteAuthority(bad)).toBeNull();
      expect(writesGloss(bad)).toBeNull();
    }
  });

  it("glosses each of the five distinctly", () => {
    const lines = WRITE_AUTHORITIES.map((w) => writesGloss(w));
    expect(lines.every((l) => !!l)).toBe(true);
    expect(new Set(lines).size).toBe(WRITE_AUTHORITIES.length);
  });
});

describe("rolesFromCatalog", () => {
  it("reads the catalog envelope and the bare array alike", () => {
    const one = [{ template: "reviewer", name: "Reviewer", role: "reviewer" }];
    expect(rolesFromCatalog(one)).toHaveLength(1);
    expect(rolesFromCatalog({ templates: one })).toHaveLength(1);
  });

  it("drops a member with no addressable key", () => {
    // A role the operator can see and cannot point a thread at is worse than
    // one that is not listed.
    const out = rolesFromCatalog([
      { name: "Nameless" },
      { template: "  ", name: "Blank" },
      { template: "planner", name: "Planner" },
    ]);
    expect(out.map((r) => r.key)).toEqual(["planner"]);
  });

  it("keeps the first of a duplicated key", () => {
    const out = rolesFromCatalog([
      { template: "planner", name: "First" },
      { template: "planner", name: "Second" },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].name).toBe("First");
  });

  it("falls back to the key when a definition carries no name", () => {
    expect(rolesFromCatalog([{ template: "planner" }])[0].name).toBe("planner");
  });

  it("keeps writes only when it is one of the closed five", () => {
    const out = rolesFromCatalog([
      { template: "a", name: "A", role: "maker" },
      { template: "b", name: "B", role: "Orchestrator" },
      { template: "c", name: "C" },
    ]);
    expect(out.map((r) => [r.name, r.writes]))
      .toEqual([["A", "maker"], ["B", null], ["C", null]]);
  });

  it("sorts by name so the list order is not the server's file order", () => {
    const out = rolesFromCatalog([
      { template: "z", name: "Zeta" },
      { template: "a", name: "Alpha" },
    ]);
    expect(out.map((r) => r.name)).toEqual(["Alpha", "Zeta"]);
  });

  it("survives every shape that is not a list", () => {
    for (const bad of [null, undefined, 0, "x", {}, { templates: 3 }]) {
      expect(rolesFromCatalog(bad)).toEqual([]);
    }
  });
});

describe("assignedThreads", () => {
  it("lists a thread that carries a role and is not armed", () => {
    // The case section 1 names explicitly. A page that showed only the armed
    // ones would hide it on the one surface that owns the setting.
    const out = assignedThreads([
      thread({ id: "a" }),
      thread({ id: "b", role: "planner" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["b"]);
  });

  it("lists a thread that is armed with no role", () => {
    const out = assignedThreads([
      thread({ id: "a" }),
      thread({ id: "b", armed: "schedule" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["b"]);
  });

  it("ignores a role that is only whitespace", () => {
    expect(assignedThreads([thread({ id: "a", role: "   " })])).toEqual([]);
  });

  it("puts armed first, then recency", () => {
    const out = assignedThreads([
      thread({ id: "old-role", role: "planner", updated_at: "2026-08-01T00:00:00Z" }),
      thread({ id: "new-role", role: "planner", updated_at: "2026-08-28T00:00:00Z" }),
      thread({ id: "armed", armed: "on-message", updated_at: "2026-01-01T00:00:00Z" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["armed", "new-role", "old-role"]);
  });

  it("breaks a timestamp tie by id, so the order is total", () => {
    const a = thread({ id: "aaa", role: "r", updated_at: "2026-08-01T00:00:00Z" });
    const b = thread({ id: "bbb", role: "r", updated_at: "2026-08-01T00:00:00Z" });
    expect(assignedThreads([b, a]).map((t) => t.id)).toEqual(["aaa", "bbb"]);
    expect(assignedThreads([a, b]).map((t) => t.id)).toEqual(["aaa", "bbb"]);
  });

  it("falls back to last_user_at when updated_at is absent", () => {
    const out = assignedThreads([
      thread({ id: "older", role: "r", updated_at: null, last_user_at: "2026-08-01T00:00:00Z" }),
      thread({ id: "newer", role: "r", updated_at: null, last_user_at: "2026-08-20T00:00:00Z" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["newer", "older"]);
  });

  it("does not mutate its input", () => {
    const input = [thread({ id: "b", role: "r" }), thread({ id: "a", role: "r" })];
    const copy = [...input];
    assignedThreads(input);
    expect(input).toEqual(copy);
  });
});

describe("roleUsage", () => {
  const threads = [
    thread({ id: "1", role: "planner", armed: "schedule" }),
    thread({ id: "2", role: "planner", armed: "off" }),
    thread({ id: "3", role: "builder", armed: "on-ready-ticket" }),
    thread({ id: "4" }),
  ];

  it("counts the threads pointing at a role and how many are armed", () => {
    expect(roleUsage(threads, "planner")).toEqual({ threads: 2, armed: 1 });
    expect(roleUsage(threads, "builder")).toEqual({ threads: 1, armed: 1 });
  });

  it("reads zero for a role nothing points at, which is a fact not an error", () => {
    expect(roleUsage(threads, "researcher")).toEqual({ threads: 0, armed: 0 });
  });

  it("matches on the trimmed key both sides", () => {
    expect(roleUsage([thread({ id: "1", role: " planner " })], "planner"))
      .toEqual({ threads: 1, armed: 0 });
  });
});

describe("armingSummary", () => {
  it("counts armed and roled independently, because they are independent", () => {
    expect(armingSummary([
      thread({ id: "1", armed: "schedule" }),
      thread({ id: "2", role: "planner" }),
      thread({ id: "3", armed: "on-message", role: "planner" }),
      thread({ id: "4" }),
    ])).toEqual({ total: 4, armed: 2, withRole: 2 });
  });

  it("is all zeros over an empty list", () => {
    expect(armingSummary([])).toEqual({ total: 0, armed: 0, withRole: 0 });
  });
});

describe("armingBody", () => {
  it("omits what was not asked about", () => {
    // Sending `role: null` on an arming-only change would wipe a role the
    // caller never mentioned. Omitted and null are different requests.
    expect(armingBody({ armed: "schedule" })).toEqual({ armed: "schedule" });
    expect(armingBody({ role: "planner" })).toEqual({ role: "planner" });
    expect(armingBody({})).toEqual({});
  });

  it("sends an explicit null to clear a role", () => {
    expect(armingBody({ role: null })).toEqual({ role: null });
    expect(armingBody({ role: "" })).toEqual({ role: null });
    expect(armingBody({ role: "   " })).toEqual({ role: null });
  });

  it("carries both when both change", () => {
    expect(armingBody({ armed: "off", role: "planner" }))
      .toEqual({ armed: "off", role: "planner" });
  });
});
