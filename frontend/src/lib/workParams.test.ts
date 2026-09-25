import { describe, expect, it } from "vitest";
import { ALL_SCOPES, nextWorkParams, parseWorkScope, parseWorkStatus } from "./workParams";

const of = (qs: string) => new URLSearchParams(qs);

describe("parseWorkScope", () => {
  it("treats an absent scope as all scopes", () => {
    expect(parseWorkScope(null)).toBe(ALL_SCOPES);
    expect(parseWorkScope("")).toBe(ALL_SCOPES);
    expect(parseWorkScope("   ")).toBe(ALL_SCOPES);
  });

  it("passes a real scope through", () => {
    expect(parseWorkScope("project:widgets")).toBe("project:widgets");
  });
});

describe("parseWorkStatus", () => {
  it("is null when absent", () => {
    expect(parseWorkStatus(null)).toBeNull();
    expect(parseWorkStatus("  ")).toBeNull();
  });

  it("passes a value through untouched", () => {
    expect(parseWorkStatus("blocked")).toBe("blocked");
  });
});

describe("nextWorkParams", () => {
  it("spells 'all scopes' by leaving the param off", () => {
    expect(nextWorkParams(of("scope=workshop"), { scope: ALL_SCOPES }).toString()).toBe("");
    expect(nextWorkParams(of("scope=workshop"), { scope: null }).toString()).toBe("");
  });

  it("sets a scope", () => {
    expect(nextWorkParams(of(""), { scope: "project:widgets" }).get("scope")).toBe("project:widgets");
  });

  it("sets and clears a status", () => {
    expect(nextWorkParams(of(""), { status: "blocked" }).get("status")).toBe("blocked");
    expect(nextWorkParams(of("status=blocked"), { status: null }).get("status")).toBeNull();
  });

  // The reason this helper exists rather than building a query string inline:
  // an unrelated param must survive a filter change.
  it("preserves params it does not own", () => {
    const out = nextWorkParams(of("job=j1&scope=workshop"), { status: "done" });
    expect(out.get("job")).toBe("j1");
    expect(out.get("scope")).toBe("workshop");
    expect(out.get("status")).toBe("done");
  });

  it("leaves a param alone when the patch does not name it", () => {
    const out = nextWorkParams(of("scope=workshop&status=blocked"), {});
    expect(out.get("scope")).toBe("workshop");
    expect(out.get("status")).toBe("blocked");
  });
});
