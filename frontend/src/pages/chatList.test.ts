import { describe, expect, it } from "vitest";
import { armedOf, chatTags, isArmed, newestFirst, visibleThreads } from "./chatList";

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

describe("armedOf", () => {
  it("defaults to off when the field is absent, which is what the wire sends today", () => {
    expect(armedOf({})).toBe("off");
    expect(armedOf({ armed: null })).toBe("off");
    expect(isArmed({})).toBe(false);
  });

  it("reads the three arming values", () => {
    expect(armedOf({ armed: "on-message" })).toBe("on-message");
    expect(armedOf({ armed: "schedule" })).toBe("schedule");
    expect(armedOf({ armed: "on-ready-ticket" })).toBe("on-ready-ticket");
    expect(isArmed({ armed: "schedule" })).toBe(true);
  });

  it("treats a value outside the closed set as off", () => {
    // Fail closed: an unknown value must not make the list claim a thread
    // works unasked.
    expect(armedOf({ armed: "sometimes" })).toBe("off");
    expect(isArmed({ armed: "sometimes" })).toBe(false);
  });
});

describe("visibleThreads", () => {
  const list = [
    row("plain", { updated_at: "2026-09-01T10:00:00Z" }),
    row("scheduled", { updated_at: "2026-08-31T10:00:00Z", armed: "schedule" }),
    row("reactive", { updated_at: "2026-09-01T11:00:00Z", armed: "on-message" }),
    row("disarmed", { updated_at: "2026-09-01T12:00:00Z", armed: "off" }),
  ];

  it("All keeps every thread, still newest first", () => {
    expect(visibleThreads(list, "all").map((t) => t.id))
      .toEqual(["disarmed", "reactive", "plain", "scheduled"]);
  });

  it("Armed keeps only the threads that advance without a person", () => {
    expect(visibleThreads(list, "armed").map((t) => t.id)).toEqual(["reactive", "scheduled"]);
  });

  it("survives a list that has not loaded", () => {
    expect(visibleThreads(null, "all")).toEqual([]);
    expect(visibleThreads(undefined, "armed")).toEqual([]);
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
    expect(chatTags({ context_key: "budget", role: "reviewer" }).map((t) => t.kind))
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
