import { beforeEach, describe, expect, it } from "vitest";
import {
  durableQueue, readHold, readQueue, restoreBatch, withoutIds, writeHold, writeQueue,
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
