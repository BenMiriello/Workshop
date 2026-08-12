import { describe, expect, it } from "vitest";
import {
  inboxElsewhere,
  inboxOverflow,
  inboxSummary,
  inboxTotal,
  inboxUrl,
  type InboxCounts,
} from "./inbox";

const counts = (q: number, r: number, n: number): InboxCounts =>
  ({ question: q, review: r, notify: n });

describe("inboxUrl", () => {
  it("reads every scope in global mode", () => {
    expect(inboxUrl(null, "global")).toBe("/api/inbox");
  });

  it("ignores a chat id in global mode", () => {
    expect(inboxUrl("chat-1", "global")).toBe("/api/inbox");
  });

  it("scopes to the thread in chat mode", () => {
    expect(inboxUrl("chat 1/2", "chat")).toBe("/api/inbox?chat_id=chat%201%2F2");
  });

  // The distinction the two modes exist for: no thread is not the global inbox.
  it("reads nothing in chat mode with no thread", () => {
    expect(inboxUrl(null, "chat")).toBe(null);
    expect(inboxUrl("", "chat")).toBe(null);
  });
});

describe("inboxTotal", () => {
  it("adds the three kinds", () => {
    expect(inboxTotal(counts(1, 2, 3))).toBe(6);
  });

  it("is zero before the first read lands", () => {
    expect(inboxTotal(null)).toBe(0);
  });
});

describe("inboxSummary", () => {
  it("names only the kinds that have something", () => {
    expect(inboxSummary(counts(1, 0, 2))).toBe("1 question, 2 notifications");
  });

  it("agrees in number", () => {
    expect(inboxSummary(counts(0, 1, 0))).toBe("1 review");
    expect(inboxSummary(counts(0, 2, 0))).toBe("2 reviews");
  });

  it("orders questions and reviews above notifications", () => {
    expect(inboxSummary(counts(1, 1, 1)))
      .toBe("1 question, 1 review, 1 notification");
  });

  it("is empty when there is nothing and when nothing has loaded", () => {
    expect(inboxSummary(counts(0, 0, 0))).toBe("");
    expect(inboxSummary(null)).toBe("");
  });
});

describe("inboxElsewhere", () => {
  it("counts what the chat filter kept out", () => {
    expect(inboxElsewhere(counts(1, 0, 0), counts(2, 1, 1), "chat")).toBe(3);
  });

  it("is zero when the chat is seeing everything", () => {
    expect(inboxElsewhere(counts(2, 1, 1), counts(2, 1, 1), "chat")).toBe(0);
  });

  // The global inbox IS everywhere, so it can never have an elsewhere, even
  // while its own counts are momentarily behind the rows on screen.
  it("is zero in global mode", () => {
    expect(inboxElsewhere(counts(1, 1, 1), counts(9, 9, 9), "global")).toBe(0);
  });

  it("never goes negative", () => {
    expect(inboxElsewhere(counts(5, 0, 0), counts(1, 0, 0), "chat")).toBe(0);
  });
});

describe("inboxOverflow", () => {
  it("is what the server holds beyond the rows it sent", () => {
    expect(inboxOverflow(counts(60, 40, 30), 100)).toBe(30);
  });

  it("is zero when every open item is on screen", () => {
    expect(inboxOverflow(counts(1, 1, 1), 3)).toBe(0);
  });

  // A row removed optimistically must not read as an item hiding on the server.
  it("never goes negative when the rows lead the counts", () => {
    expect(inboxOverflow(counts(1, 0, 0), 4)).toBe(0);
  });
});
