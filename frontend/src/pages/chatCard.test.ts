import { describe, expect, it } from "vitest";
import { cardDate, messageCount, parseListView, plainSnippet } from "./chatCard";

describe("parseListView", () => {
  it("is the grid only when the stored value says so", () => {
    expect(parseListView("grid")).toBe("grid");
    expect(parseListView("list")).toBe("list");
    expect(parseListView(null)).toBe("list");
    expect(parseListView("tiles")).toBe("list");
  });
});

describe("plainSnippet", () => {
  it("drops headings, emphasis, code marks and link targets", () => {
    const md = "## Plan\n\n**Phase 1** uses `ChatsView` and [the doc](https://x.test/a).";
    expect(plainSnippet(md)).toBe("Plan\nPhase 1 uses ChatsView and the doc.");
  });

  it("keeps one line per block and drops blank lines and fences", () => {
    const md = "First line.\n\n```ts\nconst a = 1;\n```\n- one\n- [x] two\n> quoted";
    expect(plainSnippet(md)).toBe("First line.\nconst a = 1;\none\ntwo\nquoted");
  });

  it("reads a table as its cells and skips the rule under the header", () => {
    expect(plainSnippet("| a | b |\n|---|---|\n| 1 | 2 |")).toBe("a b\n1 2");
  });

  it("leaves no syntax behind at a cut mid-link", () => {
    expect(plainSnippet("See [the plan](https://x.test/very/lo")).toBe("See the plan");
  });

  it("keeps underscores inside words", () => {
    expect(plainSnippet("set snake_case_name and _this_")).toBe("set snake_case_name and this");
  });

  it("is empty for nothing", () => {
    expect(plainSnippet("")).toBe("");
    expect(plainSnippet(null)).toBe("");
  });
});

describe("cardDate", () => {
  const now = new Date(2026, 9, 25, 15, 0);
  it("prints the time for today and the day otherwise", () => {
    expect(cardDate(new Date(2026, 9, 25, 9, 5).toISOString(), now)).toMatch(/9:05/);
    const earlier = cardDate(new Date(2026, 9, 3, 9, 5).toISOString(), now);
    expect(earlier).toMatch(/3/);
    expect(earlier).not.toMatch(/2026/);
  });

  it("carries the year for a chat from another year", () => {
    expect(cardDate(new Date(2025, 1, 3).toISOString(), now)).toMatch(/2025/);
  });

  it("is empty for a missing or bad date", () => {
    expect(cardDate(null, now)).toBe("");
    expect(cardDate("nope", now)).toBe("");
  });
});

describe("messageCount", () => {
  it("counts in words and says nothing for zero or absent", () => {
    expect(messageCount(1)).toBe("1 message");
    expect(messageCount(12)).toBe("12 messages");
    expect(messageCount(0)).toBe("");
    expect(messageCount(undefined)).toBe("");
  });
});
