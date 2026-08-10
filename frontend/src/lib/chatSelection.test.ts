import { beforeEach, describe, expect, it } from "vitest";
import {
  clearFailedFlag, durableQueue, markFailed, mergeQueue, readHold, readQueue,
  restoreBatch, withoutIds, writeHold, writeQueue,
  type QueuedMessage,
} from "./chatSelection";

// The queue's DURABLE half. Everything here is about what survives a reload, a
// closed tab or a thread switch, so it is tested against a real key/value store
// rather than through the hook that calls it.

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() { return map.size; },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    removeItem: (k: string) => { map.delete(k); },
    setItem: (k: string, v: string) => { map.set(k, String(v)); },
  } as Storage;
}

const msg = (text: string, clientId: string): QueuedMessage =>
  ({ text, clientId, sentAt: "2026-08-09T19:00:00.000Z" });

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage(), configurable: true, writable: true,
  });
});

describe("the stop hold", () => {
  // The regression this exists for: Stop, then reload or switch away and back,
  // and the queue fired everything typed ahead as an unattended turn — because
  // the hold was component state and the queue it held was in localStorage.
  it("survives being read back under the same key", () => {
    writeHold("thread-1", true);
    expect(readHold("thread-1")).toBe(true);
  });

  it("belongs to ONE thread, not to the chat surface", () => {
    writeHold("thread-1", true);
    expect(readHold("thread-2")).toBe(false);
  });

  it("clears", () => {
    writeHold("thread-1", true);
    writeHold("thread-1", false);
    expect(readHold("thread-1")).toBe(false);
  });

  it("is absent, not held, for a thread that never stopped anything", () => {
    expect(readHold("never-seen")).toBe(false);
  });
});

describe("what durable storage holds while a batch is in flight", () => {
  const a = msg("first", "c1");
  const b = msg("second", "c2");
  const typedAfter = msg("typed while it was sending", "c3");

  it("still holds the in-flight batch, ahead of what was typed after it", () => {
    // The window this closes: dispatch used to shorten the stored queue and
    // THEN send, so a tab closed during the POST lost those messages entirely.
    expect(durableQueue([a, b], [typedAfter])).toEqual([a, b, typedAfter]);
  });

  it("is just the queue once the batch is accepted", () => {
    expect(durableQueue([], [typedAfter])).toEqual([typedAfter]);
  });

  it("puts a refused batch back in front, as ONE batch", () => {
    // Order is by when the operator hit enter (invariant 5), and the batch stays
    // a batch so the retry is one turn (invariant 4).
    expect(restoreBatch([a, b], [typedAfter])).toEqual([a, b, typedAfter]);
  });

  it("does not duplicate a message that is somehow already queued", () => {
    expect(restoreBatch([a, b], [b, typedAfter])).toEqual([a, b, typedAfter]);
  });

  it("takes an accepted batch out of the thread it was drained from", () => {
    writeQueue("thread-1", [a, b, typedAfter]);
    writeQueue("thread-1", withoutIds(readQueue("thread-1"), new Set(["c1", "c2"])));
    expect(readQueue("thread-1").map((m) => m.clientId)).toEqual(["c3"]);
  });

  it("round-trips a queue with nothing left as an empty queue", () => {
    writeQueue("thread-1", [a]);
    writeQueue("thread-1", withoutIds(readQueue("thread-1"), new Set(["c1"])));
    expect(readQueue("thread-1")).toEqual([]);
  });
});

describe("two tabs on one thread", () => {
  const at = (text: string, clientId: string, sentAt: string): QueuedMessage =>
    ({ text, clientId, sentAt });

  // THE REPRO, exactly: two tabs open on thread X during a turn. Tab 1 types A,
  // so storage holds [A]. Tab 2's in-memory list is empty — A was typed
  // somewhere else — and it types B. Writing its own list over storage erased a
  // message the operator had already hit enter on.
  it("keeps a message the other tab queued", () => {
    const a = at("from tab one", "c1", "2026-08-09T19:00:00.000Z");
    const b = at("from tab two", "c2", "2026-08-09T19:00:05.000Z");
    expect(mergeQueue([a], [b], new Set())).toEqual([a, b]);
  });

  it("orders the merged queue by when enter was hit, not by which tab wrote last", () => {
    const early = at("typed first", "c1", "2026-08-09T19:00:00.000Z");
    const late = at("typed second", "c2", "2026-08-09T19:00:05.000Z");
    // The tab writing holds only the LATER message; the earlier one is news.
    expect(mergeQueue([early], [late], new Set()).map((m) => m.clientId))
      .toEqual(["c1", "c2"]);
  });

  it("does NOT resurrect an entry this tab deliberately removed", () => {
    // A drain, a drop, or an accepted batch takes an entry out. It is in `seen`,
    // so its absence from `next` is a decision, not ignorance.
    const a = at("drained", "c1", "2026-08-09T19:00:00.000Z");
    const b = at("still queued", "c2", "2026-08-09T19:00:05.000Z");
    expect(mergeQueue([a, b], [b], new Set(["c1", "c2"]))).toEqual([b]);
  });

  it("clears the queue when this tab has seen everything in storage", () => {
    const a = at("only entry", "c1", "2026-08-09T19:00:00.000Z");
    expect(mergeQueue([a], [], new Set(["c1"]))).toEqual([]);
  });

  it("prefers this tab's copy of an entry both tabs hold", () => {
    const stale = at("old text", "c1", "2026-08-09T19:00:00.000Z");
    const mine = at("old text", "c1", "2026-08-09T19:00:00.000Z");
    expect(mergeQueue([stale], [mine], new Set(["c1"]))[0]).toBe(mine);
  });

  it("sorts an entry with no send time first, because it is from an older build", () => {
    const legacy = at("pre-upgrade", "c1", "");
    const now = at("typed today", "c2", "2026-08-09T19:00:00.000Z");
    expect(mergeQueue([legacy], [now], new Set()).map((m) => m.clientId))
      .toEqual(["c1", "c2"]);
  });
});

describe("a send the server would not take", () => {
  const a = msg("first", "c1");
  const b = msg("second", "c2");

  // A refused batch used to sit in the queue looking exactly like a message
  // waiting its turn — dimmed, "sends when the current turn finishes". It does
  // not: the queue holds after a refusal and only Retry releases it.
  it("is marked, so the queue stops claiming it will send itself", () => {
    expect(markFailed([a, b]).every((m) => m.failed)).toBe(true);
  });

  it("leaves an already-marked entry alone", () => {
    const once = markFailed([a]);
    expect(markFailed(once)[0]).toBe(once[0]);
  });

  it("survives a reload, which is the whole point", () => {
    writeQueue("thread-1", markFailed([a]));
    expect(readQueue("thread-1")[0].failed).toBe(true);
  });

  it("is unmarked by Retry, and only for the entry retried", () => {
    const q = markFailed([a, b]);
    const after = clearFailedFlag(q, "c1");
    expect(after[0].failed).toBeUndefined();
    expect(after[1].failed).toBe(true);
  });

  it("leaves a queue with nothing marked untouched", () => {
    expect(clearFailedFlag([a, b], "c1")).toEqual([a, b]);
  });
});
