import { describe, expect, it } from "vitest";
import { pickupFor, pickupNote, scopeIsRunnable, type PickupMap } from "./scopePickup";

describe("scopeIsRunnable", () => {
  it("accepts exactly the three shapes launch_task_run accepts", () => {
    expect(scopeIsRunnable("workshop")).toBe(true);
    expect(scopeIsRunnable("project:birds")).toBe(true);
    expect(scopeIsRunnable("agent:keeper")).toBe(true);
    // The fourth was `build:<slug>`, and it is deleted on both sides.
    expect(scopeIsRunnable("build:thing")).toBe(false);
  });

  it("refuses a general chat scope, which is the case that used to file and fail", () => {
    expect(scopeIsRunnable("general")).toBe(false);
    expect(scopeIsRunnable("")).toBe(false);
    expect(scopeIsRunnable(null)).toBe(false);
    expect(scopeIsRunnable(undefined)).toBe(false);
  });
});

describe("pickupFor", () => {
  const map: PickupMap = {
    workshop: { scope: "workshop", runnable: true, automatic: true, why: "" },
    "project:birds": {
      scope: "project:birds",
      runnable: true,
      automatic: false,
      why: "no runner covers birds; set its autonomy to continuous or press Run",
    },
  };

  it("prefers the server's answer", () => {
    expect(pickupFor(map, "workshop").automatic).toBe(true);
    expect(pickupFor(map, "project:birds").automatic).toBe(false);
  });

  it("does not assume automatic for a scope the server did not list", () => {
    const p = pickupFor(map, "project:brand-new");
    expect(p.runnable).toBe(true);
    expect(p.automatic).toBe(false);
    expect(p.why).toMatch(/polling/);
  });

  it("falls back to the pure predicate with no map at all", () => {
    expect(pickupFor(null, "general").runnable).toBe(false);
    expect(pickupFor(undefined, "workshop").runnable).toBe(true);
  });
});

describe("pickupNote", () => {
  it("says nothing when something really will pick the ticket up", () => {
    expect(pickupNote({ scope: "workshop", runnable: true, automatic: true, why: "" })).toBeNull();
  });

  it("warns before the operator commits when the scope cannot run at all", () => {
    const note = pickupNote({
      scope: "general", runnable: false, automatic: false,
      why: "this scope cannot run tickets; file it somewhere runnable",
    });
    expect(note).toMatch(/Filing works here; running does not\./);
  });

  it("carries the server's reason for a runnable scope nothing polls", () => {
    const note = pickupNote({
      scope: "project:birds", runnable: true, automatic: false,
      why: "no runner covers birds; set its autonomy to continuous or press Run",
    });
    expect(note).toMatch(/Nothing will pick this up on its own/);
    expect(note).toMatch(/no runner covers birds/);
  });

  it("never emits an em-dash into operator-facing text", () => {
    const notes = [
      pickupNote({ scope: "general", runnable: false, automatic: false, why: "nope" }),
      pickupNote({ scope: "workshop", runnable: true, automatic: false, why: "" }),
    ];
    for (const n of notes) expect(n ?? "").not.toContain("—");
  });
});
