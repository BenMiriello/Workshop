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

  // This used to assert the opposite: that "inbox" fell back to tickets. It was
  // a pin on a dead link, not on a rule. Chat.tsx's inbox footer navigates to
  // /work?tab=inbox, the backend has served the global inbox all along, and the
  // tab now exists, so the link reaches it.
  it("reaches the global inbox tab the chat links to", () => {
    expect(parseWorkTab("inbox")).toBe("inbox");
    expect(isWorkTab("inbox")).toBe(true);
  });

  it("falls back rather than rendering an unknown tab", () => {
    expect(parseWorkTab("archive")).toBe(DEFAULT_WORK_TAB);
    expect(parseWorkTab(null)).toBe(DEFAULT_WORK_TAB);
    expect(parseWorkTab("")).toBe(DEFAULT_WORK_TAB);
  });

  it("isWorkTab narrows only the four real tabs", () => {
    expect(isWorkTab("runs")).toBe(true);
    expect(isWorkTab("archive")).toBe(false);
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
