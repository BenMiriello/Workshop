import { describe, expect, it } from "vitest";
import { armedLabel, armedOf, armedOption, ARMED_OPTIONS, isArmed, roleOf } from "./chatArmed";
import type { ThreadSummary } from "../../lib/chat";

// The claim: `armed` and `role` are INDEPENDENT, and every surface that reads
// them agrees on what an absent or unrecognised value means. The list filters
// on `isArmed` and the row prints `armedLabel`; if either invented a different
// default, a thread would show as armed in one place and not the other.

const thread = (over: Partial<ThreadSummary>) => over as ThreadSummary;

describe("what advances a thread without a person", () => {
  it("reads an absent value as off, so a thread from before the field is not armed", () => {
    expect(armedOf(thread({}))).toBe("off");
    expect(armedOf(null)).toBe("off");
    expect(isArmed(thread({}))).toBe(false);
  });

  it("reads an unrecognised value as off rather than passing it through", () => {
    // `continuous` is the retired project-autonomy word, so it is the one a
    // stale client or an old record would carry.
    expect(armedOf(thread({ armed: "continuous" }))).toBe("off");
    expect(isArmed(thread({ armed: "continuous" }))).toBe(false);
  });

  it("counts every real armed setting as armed", () => {
    for (const o of ARMED_OPTIONS) {
      expect(isArmed(thread({ armed: o.id }))).toBe(o.id !== "off");
    }
  });

  it("says nothing on a row for the ordinary case", () => {
    expect(armedLabel(thread({ armed: "off" }))).toBe("");
    expect(armedLabel(thread({ armed: "schedule" }))).toBe(armedOption("schedule").name);
  });

  it("gives every option an icon and a gloss, because the row and the picker both need them", () => {
    for (const o of ARMED_OPTIONS) {
      expect(o.name.length).toBeGreaterThan(0);
      expect(o.gloss.length).toBeGreaterThan(0);
      expect(o.Icon).toBeTruthy();
    }
  });
});

describe("role", () => {
  it("is independent of armed: a role does not arm and arming does not name one", () => {
    expect(isArmed(thread({ role: "Builder" }))).toBe(false);
    expect(roleOf(thread({ armed: "schedule" }))).toBe("");
  });

  it("trims, and an empty or absent role reads the same", () => {
    expect(roleOf(thread({ role: "  Planner  " }))).toBe("Planner");
    expect(roleOf(thread({ role: "   " }))).toBe("");
    expect(roleOf(thread({}))).toBe("");
    expect(roleOf(null)).toBe("");
  });
});
