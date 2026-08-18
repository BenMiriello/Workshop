import { describe, expect, it } from "vitest";
import { ThreadCache, type CachedBranch } from "./threadCache";
import { initialThreadState, snapshotThread, threadReducer, type LogEvent } from "./threadStore";

const ev = (seq: number, over: Partial<LogEvent> = {}): LogEvent => ({
  seq, id: `e${seq}`, ts: "2026-08-14T12:00:00Z", kind: "message",
  role: "user", content: `m${seq}`, ...over,
});

const noBranch: CachedBranch = { id: null, name: "", count: 0, head: null };

/** A snapshot with real content, so identity/content checks are meaningful. */
function snapshotFor(threadId: string, seqs: number[]) {
  const state = threadReducer(initialThreadState(threadId), {
    type: "eventsReceived",
    events: seqs.map((s) => ev(s)),
    lastSeq: seqs[seqs.length - 1] ?? 0,
    activeTurn: null,
  });
  return snapshotThread(state);
}

describe("seeding", () => {
  it("returns exactly what was set, on a hit", () => {
    const cache = new ThreadCache(10);
    const snap = snapshotFor("t1", [1, 2, 3]);
    cache.set("t1", snap, noBranch);
    const got = cache.get("t1");
    expect(got?.snapshot).toBe(snap);
    expect(got?.snapshot.order).toEqual([1, 2, 3]);
  });

  it("is undefined on a thread never set", () => {
    const cache = new ThreadCache(10);
    expect(cache.get("nope")).toBeUndefined();
  });

  it("a later set for the same thread replaces the earlier one", () => {
    const cache = new ThreadCache(10);
    cache.set("t1", snapshotFor("t1", [1]), noBranch);
    const second = snapshotFor("t1", [1, 2, 3]);
    cache.set("t1", second, noBranch);
    expect(cache.get("t1")?.snapshot).toBe(second);
    expect(cache.size).toBe(1);
  });

  it("carries the branch alongside the snapshot", () => {
    const cache = new ThreadCache(10);
    const branch: CachedBranch = { id: "b2", name: "retry", count: 4, head: "e4" };
    cache.set("t1", snapshotFor("t1", [1]), branch);
    expect(cache.get("t1")?.branch).toEqual(branch);
  });
});

describe("thread-id isolation", () => {
  it("never returns one thread's entry for another thread's id", () => {
    const cache = new ThreadCache(10);
    cache.set("a", snapshotFor("a", [1, 2]), noBranch);
    cache.set("b", snapshotFor("b", [9, 10]), noBranch);
    expect(cache.get("a")?.snapshot.order).toEqual([1, 2]);
    expect(cache.get("b")?.snapshot.order).toEqual([9, 10]);
    expect(cache.get("a")?.snapshot.threadId).toBe("a");
    expect(cache.get("b")?.snapshot.threadId).toBe("b");
  });

  it("deleting one thread leaves every other thread's entry untouched", () => {
    const cache = new ThreadCache(10);
    cache.set("a", snapshotFor("a", [1]), noBranch);
    cache.set("b", snapshotFor("b", [2]), noBranch);
    cache.delete("a");
    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
  });
});

describe("LRU eviction", () => {
  it("evicts the least recently TOUCHED entry once the limit is exceeded", () => {
    const cache = new ThreadCache(3);
    cache.set("a", snapshotFor("a", [1]), noBranch);
    cache.set("b", snapshotFor("b", [1]), noBranch);
    cache.set("c", snapshotFor("c", [1]), noBranch);
    expect(cache.size).toBe(3);
    cache.set("d", snapshotFor("d", [1]), noBranch);
    // "a" was the oldest untouched entry and is the one that goes.
    expect(cache.size).toBe(3);
    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
    expect(cache.get("c")).toBeDefined();
    expect(cache.get("d")).toBeDefined();
  });

  it("a GET bumps recency, so a just-read entry survives the next eviction", () => {
    const cache = new ThreadCache(3);
    cache.set("a", snapshotFor("a", [1]), noBranch);
    cache.set("b", snapshotFor("b", [1]), noBranch);
    cache.set("c", snapshotFor("c", [1]), noBranch);
    // Touch "a" so "b" becomes the least recently used instead.
    cache.get("a");
    cache.set("d", snapshotFor("d", [1]), noBranch);
    expect(cache.get("a")).toBeDefined();
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("c")).toBeDefined();
    expect(cache.get("d")).toBeDefined();
  });

  it("re-SET-ting an existing thread also counts as a touch, not a second entry", () => {
    const cache = new ThreadCache(2);
    cache.set("a", snapshotFor("a", [1]), noBranch);
    cache.set("b", snapshotFor("b", [1]), noBranch);
    cache.set("a", snapshotFor("a", [1, 2]), noBranch);
    cache.set("c", snapshotFor("c", [1]), noBranch);
    // "b" is now the oldest; "a" was just re-set and survives.
    expect(cache.size).toBe(2);
    expect(cache.get("a")).toBeDefined();
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("c")).toBeDefined();
  });

  it("never grows past the configured limit no matter how many threads are seen", () => {
    const cache = new ThreadCache(5);
    for (let i = 0; i < 50; i++) cache.set(`t${i}`, snapshotFor(`t${i}`, [1]), noBranch);
    expect(cache.size).toBe(5);
  });

  it("rejects a limit under 1, since an LRU of nothing evicts everything it is given", () => {
    expect(() => new ThreadCache(0)).toThrow();
  });
});
