import { describe, expect, it } from "vitest";
import {
  PAGE_SIZE, bucketFor, chatTags, groupByDate, newestFirst, pageOf, spansScopes, visibleThreads,
} from "./chatList";

// A row, cut down to the fields these rules actually read. The list route sends
// more; nothing here depends on the rest.
type Row = {
  id: string;
  updated_at?: string | null;
  armed?: string | null;
  role?: string | null;
  context_key?: string | null;
  branch_slug?: string | null;
};

const row = (id: string, extra: Partial<Row> = {}): Row => ({ id, ...extra });

describe("newestFirst", () => {
  it("is one flat list, most recently active first", () => {
    const out = newestFirst([
      row("old", { updated_at: "2026-08-01T00:00:00Z" }),
      row("new", { updated_at: "2026-09-01T12:00:00Z" }),
      row("mid", { updated_at: "2026-08-30T09:00:00Z" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["new", "mid", "old"]);
  });

  it("does not mutate its input", () => {
    const input = [
      row("a", { updated_at: "2026-08-01T00:00:00Z" }),
      row("b", { updated_at: "2026-09-01T00:00:00Z" }),
    ];
    newestFirst(input);
    expect(input.map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("sinks a thread with no timestamp rather than dropping it", () => {
    const out = newestFirst([
      row("none"),
      row("dated", { updated_at: "2026-08-01T00:00:00Z" }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["dated", "none"]);
  });
});

describe("visibleThreads", () => {
  const list = [
    row("plain", { updated_at: "2026-09-01T10:00:00Z" }),
    row("scheduled", { updated_at: "2026-08-31T10:00:00Z", armed: "schedule" }),
    row("reactive", { updated_at: "2026-09-01T11:00:00Z", armed: "on-message" }),
    row("disarmed", { updated_at: "2026-09-01T12:00:00Z", armed: "off" }),
  ];

  it("keeps every thread, newest first", () => {
    expect(visibleThreads(list).map((t) => t.id))
      .toEqual(["disarmed", "reactive", "plain", "scheduled"]);
  });

  it("does not narrow by `armed`, because the list has no filter", () => {
    // The Segmented that read this field is gone: nothing in the catalog is
    // armed, so its second position could only ever empty the list, and the
    // word was the database's rather than the operator's. A row carrying the
    // field must still appear. This is the assertion that fails if somebody
    // adds the filter back without a reason to.
    expect(visibleThreads(list)).toHaveLength(4);
    expect(visibleThreads([row("armed-one", { armed: "schedule" })]).map((t) => t.id))
      .toEqual(["armed-one"]);
  });

  it("survives a list that has not loaded", () => {
    expect(visibleThreads(null)).toEqual([]);
    expect(visibleThreads(undefined)).toEqual([]);
  });
});

describe("pageOf", () => {
  const many = Array.from({ length: 55 }, (_, i) => row(`r${i}`));

  it("mounts a bounded page and says there is more", () => {
    const p = pageOf(many, PAGE_SIZE);
    expect(p.visible).toHaveLength(PAGE_SIZE);
    expect(p.total).toBe(55);
    expect(p.more).toBe(true);
    expect(p.next).toBe(PAGE_SIZE * 2);
  });

  it("stops when the list runs out, rather than at a round number", () => {
    // The last press must not leave `shown` past the end, or the count would
    // read "showing 60 of 55".
    const p = pageOf(many, 40);
    expect(p.next).toBe(55);
    const end = pageOf(many, 55);
    expect(end.visible).toHaveLength(55);
    expect(end.more).toBe(false);
    expect(end.next).toBe(55);
  });

  it("never claims to be showing rows that are not there", () => {
    // A poll can return FEWER rows than the reader had already asked for: a
    // chat was archived, or deleted in another tab. `shown` is stale then, and
    // an unclamped slice would report a count larger than the list.
    const p = pageOf(many.slice(0, 3), 40);
    expect(p.visible).toHaveLength(3);
    expect(p.total).toBe(3);
    expect(p.more).toBe(false);
  });

  it("is empty and finished for an empty list", () => {
    const p = pageOf([], PAGE_SIZE);
    expect(p.visible).toEqual([]);
    expect(p.more).toBe(false);
    expect(p.next).toBe(0);
  });

  it("mounts nothing for a nonsense count rather than throwing", () => {
    expect(pageOf(many, -5).visible).toEqual([]);
    expect(pageOf(many, Number.NaN).visible).toEqual([]);
  });
});

describe("chatTags", () => {
  it("names the scope, the role and the branch, in that order", () => {
    expect(chatTags({ context_key: "project:workshop", role: "maker", branch_slug: "fix-chats" }))
      .toEqual([
        { kind: "scope", label: "workshop" },
        { kind: "role", label: "maker" },
        { kind: "branch", label: "fix-chats" },
      ]);
  });

  it("omits the role tag when the role is null, and prints no default", () => {
    const tags = chatTags({ context_key: "general", role: null, branch_slug: null });
    expect(tags.map((t) => t.kind)).toEqual(["scope"]);
    expect(tags.map((t) => t.label)).not.toContain("none");
  });

  it("omits the role tag when the field is absent or blank", () => {
    expect(chatTags({ context_key: "general" }).map((t) => t.kind)).toEqual(["scope"]);
    expect(chatTags({ context_key: "general", role: "  " }).map((t) => t.kind)).toEqual(["scope"]);
  });

  it("omits the branch tag when the thread is not on a branch", () => {
    expect(chatTags({ context_key: "ledger", role: "reviewer" }).map((t) => t.kind))
      .toEqual(["scope", "role"]);
  });

  it("shortens a machine-named branch, the same rule the toolbar pill uses", () => {
    const tags = chatTags({ context_key: "general", branch_slug: "t-0123456789abcdef0123456789abcdef" });
    expect(tags.find((t) => t.kind === "branch")?.label).toBe("t-01234567…");
  });

  it("always names a scope, including the unnamed root", () => {
    expect(chatTags({}).map((t) => t.label)).toEqual(["Workshop"]);
    expect(chatTags({ context_key: "development" }).map((t) => t.label)).toEqual(["Development"]);
  });
});

describe("spansScopes", () => {
  const row = (k: string | null) => ({ context_key: k });

  it("is false when every row is the same scope", () => {
    // The app-tab case: the tag would be the page's own title, once per row.
    expect(spansScopes([row("app:development"), row("app:development")])).toBe(false);
  });

  it("is false for an empty list and for one row", () => {
    expect(spansScopes([])).toBe(false);
    expect(spansScopes([row("app:development")])).toBe(false);
  });

  it("is true as soon as two rows differ", () => {
    expect(spansScopes([row("app:development"), row("app:atlas")])).toBe(true);
  });

  it("counts a missing scope as its own value, not as a match", () => {
    expect(spansScopes([row("app:development"), row(null)])).toBe(true);
  });
});

describe("chatTags scope suppression", () => {
  const t = { context_key: "app:development", role: "maker", branch_slug: "wt/x" };

  it("drops only the scope tag, keeping role and branch", () => {
    const kinds = chatTags(t, false).map((x) => x.kind);
    expect(kinds).not.toContain("scope");
    expect(kinds).toEqual(["role", "branch"]);
  });

  it("keeps the scope tag when asked, and that is the default", () => {
    expect(chatTags(t).map((x) => x.kind)).toEqual(["scope", "role", "branch"]);
    expect(chatTags(t, true).map((x) => x.kind)).toEqual(["scope", "role", "branch"]);
  });
});

describe("bucketFor", () => {
  // A fixed "now" so these assert the RULE and not the day they were run.
  const now = new Date(2026, 8, 1, 14, 0, 0); // Tue 1 Sep 2026, 14:00 local

  const at = (y: number, m: number, d: number, h = 12) =>
    new Date(y, m, d, h).toISOString();

  it("buckets by calendar day, not by elapsed hours", () => {
    // 23:00 last night is Yesterday even though it is under 24h ago at 14:00,
    // because that is what a reader means by the word.
    expect(bucketFor(at(2026, 7, 31, 23), now).label).toBe("Yesterday");
    expect(bucketFor(at(2026, 8, 1, 0), now).label).toBe("Today");
  });

  it("names the five ranges", () => {
    expect(bucketFor(at(2026, 8, 1), now).key).toBe("today");
    expect(bucketFor(at(2026, 7, 31), now).key).toBe("yesterday");
    expect(bucketFor(at(2026, 7, 28), now).key).toBe("week");
    expect(bucketFor(at(2026, 7, 20), now).key).toBe("month");
    expect(bucketFor(at(2026, 5, 1), now).key).toBe("older");
  });

  it("puts a missing or unreadable date in the last bucket, never nowhere", () => {
    // A chat with a bad timestamp is still a chat. A list that silently drops
    // rows is the failure this surface keeps having.
    expect(bucketFor(null, now).key).toBe("older");
    expect(bucketFor(undefined, now).key).toBe("older");
    expect(bucketFor("not a date", now).key).toBe("older");
  });
});

describe("groupByDate", () => {
  const now = new Date(2026, 8, 1, 14, 0, 0);
  const row = (id: string, iso: string | null) => ({ id, updated_at: iso });

  it("keeps the order it was given and never reorders a row", () => {
    const rows = [
      row("a", new Date(2026, 8, 1, 13).toISOString()),
      row("b", new Date(2026, 8, 1, 9).toISOString()),
      row("c", new Date(2026, 7, 31, 9).toISOString()),
    ];
    const g = groupByDate(rows, now);
    expect(g.map((x) => x.label)).toEqual(["Today", "Yesterday"]);
    expect(g[0].items.map((x) => x.id)).toEqual(["a", "b"]);
    expect(g[1].items.map((x) => x.id)).toEqual(["c"]);
  });

  it("emits no heading for a bucket with nothing in it", () => {
    const g = groupByDate([row("a", new Date(2026, 7, 20).toISOString())], now);
    expect(g.map((x) => x.key)).toEqual(["month"]);
  });

  it("orders the groups oldest last, whatever order the rows arrived in", () => {
    const g = groupByDate([
      row("old", new Date(2026, 5, 1).toISOString()),
      row("new", new Date(2026, 8, 1).toISOString()),
    ], now);
    expect(g.map((x) => x.key)).toEqual(["today", "older"]);
  });

  it("loses no rows", () => {
    const rows = [
      row("a", new Date(2026, 8, 1).toISOString()),
      row("b", null),
      row("c", new Date(2026, 7, 28).toISOString()),
    ];
    const total = groupByDate(rows, now).reduce((n, g) => n + g.items.length, 0);
    expect(total).toBe(3);
  });
});
