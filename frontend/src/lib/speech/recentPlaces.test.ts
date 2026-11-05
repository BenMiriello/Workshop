import { describe, expect, it } from "vitest";
import { addRecent, parseRecent, RECENT_MAX, type RecentPlace } from "./recentPlaces";

const field = (key: string): RecentPlace => ({
  kind: "field", name: key, identity: { path: "/p", key, index: 0 }, href: "/p",
});
const chat = (draftKey: string): RecentPlace => ({ kind: "chat", name: draftKey, draftKey, threadId: null, scope: "s" });

describe("addRecent", () => {
  it("puts the newest first, once each, up to the limit", () => {
    let list: RecentPlace[] = [];
    list = addRecent(list, field("a"));
    list = addRecent(list, chat("s::1"));
    list = addRecent(list, field("a"));
    expect(list.map((p) => p.name)).toEqual(["a", "s::1"]);
    for (let i = 0; i < 20; i++) list = addRecent(list, field(`k${i}`));
    expect(list).toHaveLength(RECENT_MAX);
    expect(list[0].name).toBe("k19");
  });
});

describe("parseRecent", () => {
  it("keeps only places and survives junk", () => {
    expect(parseRecent(JSON.stringify([field("a"), { kind: "field" }, chat("s::2"), 3]))).toHaveLength(2);
    expect(parseRecent("{bad")).toEqual([]);
  });
});
