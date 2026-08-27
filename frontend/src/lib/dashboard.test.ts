import { describe, it, expect } from "vitest";
import {
  continueRows, formatTokens, moreLabel, needsRows, runningRows,
  spendLine, type InboxList, type ThreadSummary,
} from "./dashboard";
import type { Job } from "./jobs";

const thread = (over: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id: "t1", context_key: "development", scope: "development", mode: "build",
  title: "ingest retry backoff", message_count: 4,
  last_message: "the queue drains but rows land twice", ...over,
} as ThreadSummary);

const item = (over: Partial<InboxList["items"][number]> = {}) => ({
  id: "task:development:abc", kind: "question", state: "open",
  scope: "development", title: "Should a refresh extend the session?",
  body: "Refresh tokens expire with the session today.", options: [],
  ...over,
} as InboxList["items"][number]);

describe("what needs you", () => {
  it("leads with who is asking, and falls back to the scope", () => {
    const rows = needsRows({ items: [item({ agent: "reviewer" }), item({ id: "b" })],
                             counts: {}, total: 2 });
    expect(rows.map((r) => r.who)).toEqual(["reviewer", "development"]);
  });

  it("does not print the scope twice when it is standing in for the agent", () => {
    const rows = needsRows({ items: [item(), item({ id: "b", agent: "reviewer" })],
                            counts: {}, total: 2 });
    expect(rows[0].scope).toBe("");          // `who` is already "development"
    expect(rows[1].scope).toBe("development");
  });

  it("opens the ticket when there is one, and the list when there is not", () => {
    const rows = needsRows({
      items: [item({ ticket_id: "abc" }), item({ id: "n1", kind: "notify" })],
      counts: {}, total: 2,
    });
    expect(rows[0].to).toBe("/tickets/development/abc");
    // An ambient notice has no record of its own. Sending it to an invented
    // page would be a nav row that lies; the waiting list is a real answer.
    expect(rows[1].to).toBe("/tickets");
  });

  it("says what it wants, because the card mixes two different requests", () => {
    // Five of nine rows on the live instance were finished work waiting to be
    // accepted, not questions, and the row was identical for both.
    const rows = needsRows({
      items: [item({ kind: "question" }), item({ id: "b", kind: "review" })],
      counts: {}, total: 2,
    });
    expect(rows.map((r) => r.wants)).toEqual(["asks", "for review"]);
  });

  it("says one line, never a paragraph, on a fixed-height row", () => {
    const rows = needsRows({ items: [item({ title: "line one\nline two" })],
                             counts: {}, total: 1 });
    expect(rows[0].ask).toBe("line one");
  });
});

describe("pick up where you left off", () => {
  it("carries the live flag and the context pair straight off the row", () => {
    const rows = continueRows([thread({ running: true, context_tokens: 250_000,
                                        context_window: 1_000_000 })]);
    expect(rows[0].running).toBe(true);
    expect(rows[0].used).toBe(250_000);
    expect(rows[0].win).toBe(1_000_000);
  });

  it("reports no reading rather than zero when no turn has completed", () => {
    // 0% and "no measurement yet" are different facts and used to render the
    // same string. The ring is absent here, not empty.
    const rows = continueRows([thread()]);
    expect(rows[0].used).toBeNull();
    expect(rows[0].win).toBeNull();
  });

  it("links into the thread it came from", () => {
    const rows = continueRows([thread({ id: "abc", context_key: "app:atlas" })]);
    expect(rows[0].to).toBe("/chat/app%3Aatlas?thread=abc");
  });

  it("never renders an empty title", () => {
    expect(continueRows([thread({ title: "" })])[0].title).toBe("Untitled chat");
  });
});

describe("running now", () => {
  const job = (over: Partial<Job> = {}): Job => ({
    id: "j1", status: "running", agent_name: "ingest-sweep",
    summary: "Draining the dead letter table", started_at: "2026-08-26T05:00:00Z",
    ...over,
  } as Job);

  it("leads with who is running, not with a task id", () => {
    expect(runningRows([job()], null)[0].name).toBe("ingest-sweep");
  });

  it("falls back to the trigger when no agent is named", () => {
    expect(runningRows([job({ agent_name: undefined, trigger_label: "Cron" })], null)[0].name)
      .toBe("Cron");
  });

  it("counts a chat with a turn in flight as running", () => {
    // "Nothing running" on an instance with two live turns is the kind of
    // confident wrong answer that teaches an operator to stop reading a card.
    const rows = runningRows([], [thread({ running: true }), thread({ id: "t2" })]);
    expect(rows.map((r) => r.name)).toEqual(["ingest retry backoff"]);
    expect(rows[0].to).toContain("thread=t1");
  });

  it("puts runs above chats", () => {
    const rows = runningRows([job()], [thread({ running: true })]);
    expect(rows.map((r) => r.name)).toEqual(["ingest-sweep", "ingest retry backoff"]);
  });
});

describe("the spend line", () => {
  it("is tokens and says which window", () => {
    // NOT money. The ledger counts tokens and no price table exists, so a
    // dollar figure would be invented.
    expect(spendLine({ app_used: 1_970_000, app_total: 5_000_000 } as never))
      .toBe("1.97M of 5.00M · 5h");
  });

  it("omits the ceiling on an uncapped window rather than printing null", () => {
    expect(spendLine({ app_used: 12_400, app_total: null } as never)).toBe("12K · 5h");
  });

  it("says nothing at all before the read lands", () => {
    expect(spendLine(null)).toBe("");
  });

  it("rounds the way the usage page rounds", () => {
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1_500)).toBe("2K");
    expect(formatTokens(2_400_000)).toBe("2.40M");
  });
});

describe("what a card leaves out", () => {
  // A card scrolls, so it never leaves a row out for want of room. The only
  // thing it can be missing is what the server's own page cap withheld.
  it("names the server's remainder on the link, and stays quiet when there is none", () => {
    expect(moreLabel(4, "inbox")).toBe("4 more inbox →");
    expect(moreLabel(0, "inbox")).toBe("inbox →");
  });
});
