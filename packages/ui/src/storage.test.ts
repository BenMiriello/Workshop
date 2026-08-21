import { beforeEach, describe, expect, it } from "vitest";
import {
  _resetCurrentUserForTest, _resetDeviceScopedForTest, isDeviceScoped, isNamespacedKey,
  namespacedKey, planMigration, registerDeviceScoped, setCurrentUser, userStorage,
} from "./storage";

// The generic namespaced-storage engine: key construction, the pre-auth case,
// the migration case, two users never colliding, and the registration hook a
// host uses to declare its own device-scoped keys without this module
// knowing what any of them are for.

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
  _resetDeviceScopedForTest();
});

describe("with no storage at all", () => {
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
  it("is exactly ws:<user_id>:<key>", () => {
    expect(namespacedKey(7, "draft:general")).toBe("ws:7:draft:general");
  });

  it("falls back to the anon bucket for a null (pre-auth) user", () => {
    expect(namespacedKey(null, "foo")).toBe("ws:anon:foo");
  });

  it("two different user ids can never collide on the same logical key", () => {
    const a = namespacedKey(1, "draft:general");
    const b = namespacedKey(2, "draft:general");
    expect(a).not.toBe(b);
  });
});

describe("isNamespacedKey", () => {
  it("recognizes the module's own shape", () => {
    expect(isNamespacedKey("ws:5:outbox:t1")).toBe(true);
  });

  it("does not mistake the one-colon migration flag for a namespaced key", () => {
    expect(isNamespacedKey("ws:legacy-migrated")).toBe(false);
  });

  it("does not mistake an ordinary legacy key for a namespaced one", () => {
    expect(isNamespacedKey("outbox:t1")).toBe(false);
    expect(isNamespacedKey("some_balance")).toBe(false);
  });
});

describe("registerDeviceScoped", () => {
  it("ships with zero built-in exemptions", () => {
    expect(isDeviceScoped("anything")).toBe(false);
  });

  it("exempts a key once its owner registers it, with a reason", () => {
    registerDeviceScoped([{ match: (k) => k === "deviceToken", reason: "device identity" }]);
    expect(isDeviceScoped("deviceToken")).toBe(true);
    expect(isDeviceScoped("somethingElse")).toBe(false);
  });

  it("is additive across multiple registrants", () => {
    registerDeviceScoped([{ match: (k) => k === "a", reason: "one owner" }]);
    registerDeviceScoped([{ match: (k) => k === "b", reason: "another owner" }]);
    expect(isDeviceScoped("a")).toBe(true);
    expect(isDeviceScoped("b")).toBe(true);
  });
});

describe("planMigration", () => {
  it("moves an unnamespaced key to the user's bucket", () => {
    const plan = planMigration(["old-key"], 3);
    expect(plan).toEqual([{ from: "old-key", to: "ws:3:old-key" }]);
  });

  it("leaves an already-namespaced key alone", () => {
    expect(planMigration(["ws:3:old-key"], 3)).toEqual([]);
  });

  it("leaves an exempt key alone", () => {
    expect(planMigration(["device"], 3, (k) => k === "device")).toEqual([]);
  });
});

describe("setCurrentUser: the one-time migration", () => {
  it("moves a legacy key into the signed-in user's namespace", () => {
    localStorage.setItem("draft", "hello");
    setCurrentUser(9);
    expect(localStorage.getItem("draft")).toBeNull();
    expect(localStorage.getItem("ws:9:draft")).toBe("hello");
  });

  it("does not re-migrate on a later call", () => {
    localStorage.setItem("draft", "first");
    setCurrentUser(9);
    localStorage.setItem("draft", "leaked-in-later"); // simulates old code writing raw
    setCurrentUser(9);
    // The flag stops a second sweep; the raw key is left exactly as written.
    expect(localStorage.getItem("draft")).toBe("leaked-in-later");
  });

  it("leaves a registered device-scoped key un-namespaced through userStorage", () => {
    registerDeviceScoped([{ match: (k) => k === "deviceToken", reason: "device identity" }]);
    setCurrentUser(9);
    userStorage.local.setItem("deviceToken", "abc");
    expect(localStorage.getItem("deviceToken")).toBe("abc");
    expect(localStorage.getItem("ws:9:deviceToken")).toBeNull();
  });
});

describe("userStorage: reads and writes go through the active user's namespace", () => {
  it("uses the anon bucket before setCurrentUser is ever called", () => {
    userStorage.local.setItem("k", "v");
    expect(localStorage.getItem("ws:anon:k")).toBe("v");
  });

  it("switches bucket when the active user changes", () => {
    setCurrentUser(1);
    userStorage.local.setItem("k", "one");
    setCurrentUser(2);
    userStorage.local.setItem("k", "two");
    expect(userStorage.local.getItem("k")).toBe("two");
    setCurrentUser(1);
    expect(userStorage.local.getItem("k")).toBe("one");
  });
});
