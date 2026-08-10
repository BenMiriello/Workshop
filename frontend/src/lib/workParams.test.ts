import { describe, expect, it } from "vitest";
import {
  ALL_SCOPES, DEFAULT_WORK_TAB, isWorkTab, nextWorkParams, parseWorkScope, parseWorkTab,
} from "./workParams";

describe("parseWorkTab", () => {
  it("reads the tab five navigations already write", () => {
    expect(parseWorkTab("runs")).toBe("runs");
    expect(parseWorkTab("tickets")).toBe("tickets");
    expect(parseWorkTab("review")).toBe("review");
  });

  it("falls back rather than rendering an unknown tab", () => {
    // Chat.tsx navigates to ?tab=inbox, which is not a tab on this page.
    expect(parseWorkTab("inbox")).toBe(DEFAULT_WORK_TAB);
    expect(parseWorkTab(null)).toBe(DEFAULT_WORK_TAB);
    expect(parseWorkTab("")).toBe(DEFAULT_WORK_TAB);
  });

  it("isWorkTab narrows only the three real tabs", () => {
    expect(isWorkTab("runs")).toBe(true);
    expect(isWorkTab("inbox")).toBe(false);
    expect(isWorkTab(undefined)).toBe(false);
  });
});

describe("parseWorkScope", () => {
  it("treats an absent scope as no filter", () => {
    expect(parseWorkScope(null)).toBe(ALL_SCOPES);
    expect(parseWorkScope("  ")).toBe(ALL_SCOPES);
  });

  it("passes a real scope through verbatim", () => {
    expect(parseWorkScope("project:workshop-ui")).toBe("project:workshop-ui");
  });
});

describe("nextWorkParams", () => {
  const of = (s: string) => new URLSearchParams(s);

  it("keeps the scope when only the tab changes", () => {
    const out = nextWorkParams(of("scope=project:birds"), { tab: "runs" });
    expect(out.get("tab")).toBe("runs");
    expect(out.get("scope")).toBe("project:birds");
  });

  it("keeps the tab when only the scope changes", () => {
    const out = nextWorkParams(of("tab=review"), { scope: "workshop" });
    expect(out.get("tab")).toBe("review");
    expect(out.get("scope")).toBe("workshop");
  });

  it("spells the defaults by absence, so an unfiltered list is plain /work", () => {
    const out = nextWorkParams(of("tab=runs&scope=workshop"), {
      tab: DEFAULT_WORK_TAB,
      scope: ALL_SCOPES,
    });
    expect(out.toString()).toBe("");
  });

  it("leaves untouched params alone", () => {
    const out = nextWorkParams(of("job=j1&tab=tickets"), { tab: "runs" });
    expect(out.get("job")).toBe("j1");
  });

  it("deletes on null", () => {
    const out = nextWorkParams(of("tab=runs&scope=workshop"), { scope: null });
    expect(out.has("scope")).toBe(false);
    expect(out.get("tab")).toBe("runs");
  });
});
