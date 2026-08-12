import { beforeEach, describe, expect, it } from "vitest";
import {
  mergeOutbox, readOutbox, resolveScope, writeOutbox,
  type OutboxSend,
} from "./chatSelection";

// THE OUTBOX: sends this browser made that the server has not taken.
//
// What used to be tested here was a type-ahead QUEUE — messages waiting on a
// running turn, stored per thread, sent later by whichever tab happened to be
// looking. Eleven invariants accumulated on it, each from a real report, and
// none of them could fix the thing that was actually wrong: one browser's
// localStorage is not storage. On 2026-08-12 the operator sent two long
// messages during a `/compact`, moved to another chat, and came back on a
// different device. They were gone.
//
// The queue is the server's now (backend/services/chat_queue.py, and its tests
// are the ones that pin queueing). What is left here is the ONE case the server
// cannot know about, because the request never reached it — and the properties
// below are the ones that make that case survivable.

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

const send = (text: string, clientId: string, ts = "2026-08-09T19:00:00.000Z"): OutboxSend =>
  ({ text, clientId, ts, status: "failed", at: 1, afterSeq: 0 });

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage(), configurable: true, writable: true,
  });
});

describe("a send the server never took", () => {
  it("survives a reload, which is the whole point", () => {
    // The composer is cleared synchronously when the operator hits enter, so
    // between that moment and the server's answer this is the only copy of what
    // they wrote. A crash there used to lose it outright.
    writeOutbox("t1", [send("the long one", "c1")]);
    expect(readOutbox("t1").map((s) => s.text)).toEqual(["the long one"]);
  });

  it("comes back as failed, never as still-sending", () => {
    // Whatever it was doing when the tab went away, nothing is carrying it now.
    // "Sending" would be a lie with a spinner on it; "failed" is true and it is
    // the state that offers a Retry.
    writeOutbox("t1", [{ ...send("hello", "c1"), status: "sending" }]);
    expect(readOutbox("t1")[0].status).toBe("failed");
  });

  it("keeps the files it carried, so a retry re-sends the same message", () => {
    const withFile: OutboxSend = {
      ...send("look at this", "c1"),
      attachments: [{ id: "a1", name: "shot.png", kind: "image" }],
    };
    writeOutbox("t1", [withFile]);
    expect(readOutbox("t1")[0].attachments).toEqual(
      [{ id: "a1", name: "shot.png", kind: "image" }]);
  });

  it("keeps WHY it failed, because a reload used to turn the reason into a guess", () => {
    writeOutbox("t1", [{ ...send("hello", "c1"), error: "developer mode is off" }]);
    expect(readOutbox("t1")[0].error).toBe("developer mode is off");
  });

  it("belongs to ONE thread, not to the chat surface", () => {
    writeOutbox("t1", [send("for t1", "c1")]);
    expect(readOutbox("t2")).toEqual([]);
  });

  it("leaves no key behind when there is nothing left to hold", () => {
    writeOutbox("t1", [send("hello", "c1")]);
    writeOutbox("t1", []);
    expect(localStorage.getItem("ws-chat-outbox:t1")).toBeNull();
  });

  it("reads unparseable storage as empty rather than throwing", () => {
    localStorage.setItem("ws-chat-outbox:t1", "{not json");
    expect(readOutbox("t1")).toEqual([]);
  });
});

describe("two tabs on one thread", () => {
  // Two tabs on one chat is ordinary — the operator keeps a focused chat open
  // beside the app — and both write the same key. A tab that wrote its whole
  // in-memory list over whatever was there erased a send made in the other one.
  it("keeps a send the other tab made", () => {
    const stored = [send("from the other tab", "other")];
    const mine = [send("mine", "c1")];
    expect(mergeOutbox(stored, mine, new Set(["c1"])).map((s) => s.clientId))
      .toEqual(["c1", "other"]);
  });

  it("does NOT resurrect a send this tab removed on purpose", () => {
    // Removed because the server took it. A plain union would bring back every
    // message that had already been delivered.
    const stored = [send("already accepted", "c1")];
    expect(mergeOutbox(stored, [], new Set(["c1"]))).toEqual([]);
  });

  it("orders the merged list by when enter was hit", () => {
    const stored = [send("typed first", "other", "2026-08-09T19:00:00.000Z")];
    const mine = [send("typed second", "c1", "2026-08-09T19:05:00.000Z")];
    expect(mergeOutbox(stored, mine, new Set(["c1"])).map((s) => s.text))
      .toEqual(["typed first", "typed second"]);
  });

  it("prefers this tab's copy of a send both tabs hold", () => {
    const stored = [{ ...send("stale", "c1"), error: "old" }];
    const mine = [{ ...send("current", "c1"), error: "new" }];
    const merged = mergeOutbox(stored, mine, new Set(["c1"]));
    expect(merged).toHaveLength(1);
    expect(merged[0].error).toBe("new");
  });
});

describe("resolveScope", () => {
  const base = {
    contextKey: "general",
    knownScopes: ["general", "development", "budget"],
    linkedThreadHome: null,
    startNew: false,
    remembered: null,
  };

  it("defaults the unscoped surface to development in a dev build", () => {
    expect(resolveScope(base, true)).toBe("development");
  });

  it("keeps the ordinary general default outside a dev build", () => {
    expect(resolveScope(base, false)).toBe("general");
  });

  it("does not default to development if it isn't a known scope (dev flag on, backend not in dev mode)", () => {
    expect(resolveScope({ ...base, knownScopes: ["general", "budget"] }, true)).toBe("general");
  });

  it("a specifically-scoped surface (a project/app dock) is unaffected", () => {
    expect(resolveScope({ ...base, contextKey: "budget", knownScopes: [...base.knownScopes] }, true))
      .toBe("budget");
  });

  it("an explicit past pick still wins, including picking general on purpose", () => {
    expect(resolveScope({ ...base, remembered: "general" }, true)).toBe("general");
    expect(resolveScope({ ...base, remembered: "budget" }, true)).toBe("budget");
  });

  it("New chat in dev mode starts on development, not general", () => {
    expect(resolveScope({ ...base, startNew: true, remembered: "budget" }, true)).toBe("development");
  });

  it("a linked thread's home still outranks the dev default", () => {
    expect(resolveScope({ ...base, linkedThreadHome: "budget" }, true)).toBe("budget");
  });
});
