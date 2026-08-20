import { beforeEach, describe, expect, it } from "vitest";
import {
  _resetCurrentUserForTest, isDeviceScoped, isNamespacedKey, namespacedKey,
  planMigration, setCurrentUser, userStorage,
} from "./storage";

// ADR 0025 clause 4: every localStorage/sessionStorage key is prefixed with
// the acting user's id, `ws:<user_id>:<key>`, through this module and nothing
// else. The four things this file is asked to pin down (per the module's own
// header): key construction, the pre-auth case, the migration case, and that
// two different user ids cannot collide.

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

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage(), configurable: true, writable: true,
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    value: memoryStorage(), configurable: true, writable: true,
  });
  _resetCurrentUserForTest();
});

describe("with no storage at all", () => {
  // Server rendering has neither global. A bare `localStorage` reference is
  // what used to throw here: it is a REFERENCE, so it fails before any
  // try/catch around the call can help, and it took the whole shell down with
  // it (found 2026-08-20 by the shell's mount test).
  beforeEach(() => {
    Reflect.deleteProperty(globalThis, "localStorage");
    Reflect.deleteProperty(globalThis, "sessionStorage");
  });

  it("reads null, writes nothing, and reports unavailable", () => {
    expect(() => userStorage.local.getItem("k")).not.toThrow();
    expect(userStorage.local.getItem("k")).toBeNull();
    expect(() => userStorage.local.setItem("k", "v")).not.toThrow();
    expect(() => userStorage.local.removeItem("k")).not.toThrow();
    expect(userStorage.local.available()).toBe(false);
    expect(userStorage.session.getItem("k")).toBeNull();
    expect(userStorage.session.available()).toBe(false);
  });
});

describe("namespacedKey", () => {
  it("is exactly ws:<user_id>:<key>, ADR 0025 clause 4's own words", () => {
    expect(namespacedKey(7, "ws-chat-sel:general")).toBe("ws:7:ws-chat-sel:general");
  });

  it("falls back to the anon bucket for a null (pre-auth) user", () => {
    expect(namespacedKey(null, "foo")).toBe("ws:anon:foo");
  });

  it("two different user ids can never collide on the same logical key", () => {
    const a = namespacedKey(1, "ws-chat-draft:general");
    const b = namespacedKey(2, "ws-chat-draft:general");
    expect(a).not.toBe(b);
  });
});

describe("isNamespacedKey", () => {
  it("recognizes the module's own shape", () => {
    expect(isNamespacedKey("ws:5:ws-chat-outbox:t1")).toBe(true);
  });

  it("does not mistake the one-colon migration flag for a namespaced key", () => {
    expect(isNamespacedKey("ws:legacy-migrated")).toBe(false);
  });

  it("does not mistake an ordinary legacy key for a namespaced one", () => {
    expect(isNamespacedKey("ws-chat-outbox:t1")).toBe(false);
    expect(isNamespacedKey("ws_finance_bal")).toBe(false);
  });
});

describe("isDeviceScoped", () => {
  it("exempts the bird recorder's own device identity", () => {
    expect(isDeviceScoped("birdDeviceToken")).toBe(true);
    expect(isDeviceScoped("birdStationToken")).toBe(true); // pre-rename legacy name
  });

  it("exempts the public species-info cache", () => {
    expect(isDeviceScoped("birdinfo:Turdus migratorius")).toBe(true);
  });

  it("exempts the chunk-reload guard", () => {
    expect(isDeviceScoped("ws-chunk-reloaded")).toBe(true);
  });

  it("does not exempt an ordinary data key", () => {
    expect(isDeviceScoped("ws-chat-outbox:t1")).toBe(false);
    expect(isDeviceScoped("ws_finance_bal")).toBe(false);
  });
});

describe("planMigration", () => {
  it("moves a legacy key to the signed-in user's namespace", () => {
    const plan = planMigration(["ws-chat-outbox:t1"], 3);
    expect(plan).toEqual([{ from: "ws-chat-outbox:t1", to: "ws:3:ws-chat-outbox:t1" }]);
  });

  it("leaves an already-namespaced key alone", () => {
    expect(planMigration(["ws:3:ws-chat-outbox:t1"], 3)).toEqual([]);
  });

  it("leaves a device-scoped key alone", () => {
    expect(planMigration(["birdDeviceToken", "birdinfo:x"], 3)).toEqual([]);
  });

  it("leaves the migration flag itself alone", () => {
    expect(planMigration(["ws:legacy-migrated"], 3)).toEqual([]);
  });

  it("plans every ordinary key in one pass", () => {
    const plan = planMigration(
      ["ws-chat-sel:general", "birdDeviceToken", "ws:9:already-done", "ws_finance_bal"],
      9,
    );
    expect(plan.map((p) => p.from).sort()).toEqual(["ws-chat-sel:general", "ws_finance_bal"].sort());
  });
});

describe("the pre-auth case", () => {
  it("a read before any setCurrentUser call lands in the anon bucket, not a crash", () => {
    expect(userStorage.local.getItem("some-key")).toBeNull();
    userStorage.local.setItem("some-key", "v");
    expect(localStorage.getItem(namespacedKey(null, "some-key"))).toBe("v");
  });

  it("an anon write can never be read back as a real user's data", () => {
    userStorage.local.setItem("draft", "typed before login");
    setCurrentUser(42);
    // The anon bucket is a different key entirely; user 42 sees nothing.
    expect(userStorage.local.getItem("draft")).toBeNull();
  });
});

describe("two different user ids", () => {
  it("cannot read or clobber each other's values through the module", () => {
    setCurrentUser(1);
    userStorage.local.setItem("draft", "user one's draft");
    setCurrentUser(2);
    expect(userStorage.local.getItem("draft")).toBeNull();
    userStorage.local.setItem("draft", "user two's draft");
    setCurrentUser(1);
    expect(userStorage.local.getItem("draft")).toBe("user one's draft");
  });

  it("works the same way for sessionStorage", () => {
    setCurrentUser(1);
    userStorage.session.setItem("scroll:t1", "100");
    setCurrentUser(2);
    expect(userStorage.session.getItem("scroll:t1")).toBeNull();
  });
});

describe("the migration case", () => {
  it("moves a legacy key into the first signed-in user's namespace", () => {
    localStorage.setItem("ws-chat-sel:general", "thread-42");
    setCurrentUser(5);
    expect(userStorage.local.getItem("ws-chat-sel:general")).toBe("thread-42");
    // The old, un-namespaced key is gone — a MOVE, not a copy (see header).
    expect(localStorage.getItem("ws-chat-sel:general")).toBeNull();
  });

  it("runs across both storage areas", () => {
    localStorage.setItem("ws_finance_bal", "{}");
    sessionStorage.setItem("ws-chat-scroll:t1", "{}");
    setCurrentUser(5);
    expect(userStorage.local.getItem("ws_finance_bal")).toBe("{}");
    expect(userStorage.session.getItem("ws-chat-scroll:t1")).toBe("{}");
  });

  it("does not touch a device-scoped key", () => {
    localStorage.setItem("birdDeviceToken", "abc-123");
    setCurrentUser(5);
    // Untouched at its own raw key — never namespaced, never moved.
    expect(localStorage.getItem("birdDeviceToken")).toBe("abc-123");
    expect(userStorage.local.getItem("birdDeviceToken")).toBe("abc-123");
  });

  it("only consumes a legacy key ONCE — a second account starts clean", () => {
    localStorage.setItem("ws-chat-sel:general", "thread-42");
    setCurrentUser(1); // first signed-in user vacuums up the pre-existing data
    expect(userStorage.local.getItem("ws-chat-sel:general")).toBe("thread-42");

    setCurrentUser(2); // a second account on the same browser, same key
    expect(userStorage.local.getItem("ws-chat-sel:general")).toBeNull();
  });

  it("is idempotent: signing the same user in again does not re-scan or duplicate", () => {
    localStorage.setItem("ws-chat-sel:general", "thread-42");
    setCurrentUser(1);
    // A second, unrelated legacy key shows up later (e.g. a key this module
    // did not know about yet). Because migration already ran once for this
    // browser, it must NOT sweep this up under user 1 automatically —
    // otherwise a stray key written after the flag is set would look migrated
    // when it was simply never touched.
    localStorage.setItem("some-later-legacy-key", "v");
    setCurrentUser(1);
    expect(localStorage.getItem("some-later-legacy-key")).toBe("v");
  });

  it("never overwrites a value already sitting at the destination", () => {
    // Both the namespaced destination AND a legacy raw key exist when
    // migration runs (e.g. another tab already migrated this account). The
    // real value must win over the stale legacy one, not the other way round.
    localStorage.setItem(namespacedKey(1, "ws-chat-sel:general"), "already set for user 1");
    localStorage.setItem("ws-chat-sel:general", "stale legacy value");
    setCurrentUser(1);
    expect(userStorage.local.getItem("ws-chat-sel:general")).toBe("already set for user 1");
  });
});
