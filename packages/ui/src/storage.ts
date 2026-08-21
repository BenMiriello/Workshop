// A namespaced localStorage/sessionStorage engine for a multi-user,
// single-page app. Originated as Workshop's `lib/storage.ts` (ADR 0025 clause
// 4: "Every localStorage and sessionStorage key is prefixed with the acting
// user's id: ws:<user_id>:<key>."), promoted here because the mechanism
// itself, namespace-by-active-user plus a one-time legacy-key migration plus
// a small set of keys that opt out of namespacing on purpose, is general: any
// app-hosting shell with more than one signed-in account on a shared device
// needs it, not just Workshop.
//
// WHAT STAYS OUT OF THIS FILE, ON PURPOSE. The actual exemption list (which
// keys are device-scoped rather than user-scoped, and why) is Workshop's own
// answer, and several of today's entries name a specific installed app. That
// content has no business in a package every app and an eventual out-of-repo
// consumer resolves, so it is NOT here: it is registered at runtime by
// `registerDeviceScoped()`, called from Workshop's own `frontend/src/lib/
// storage.ts` (which also owns the one Workshop-specific migration key, "the
// chunk-reload guard"). This module ships with zero built-in exemptions.
//
// SINGLETON, DELIBERATELY. `userStorage`, `setCurrentUser` and
// `registerDeviceScoped` are module-level state, not a factory, because every
// importer of `@workshop/ui` in one bundle resolves to the same module
// instance: the shell calls `setCurrentUser` once (from its auth boot) and
// every app page that reads `userStorage` afterwards sees the same active
// user, with no wiring required to thread it through props or context.
//
// THE BOOTSTRAP PROBLEM. A read that happens before the signed-in user is
// known cannot be told apart from a read for the wrong user. `setCurrentUser`
// defaults to `null`, and every call made before the first real
// `setCurrentUser(id)` reads and writes under the literal bucket
// `ws:anon:<key>`, which can never collide with a real account's data (a real
// user id never stringifies to "anon"). It is the host's job to guarantee
// this never happens for an authed page (Workshop's `<Guard>` does), but nothing
// in this module trusts that from the outside.
//
// THE PRE-EXISTING-KEYS PROBLEM. Every key already sitting in a browser
// profile from before namespacing began has no owner recorded. So: MIGRATE
// ONCE, ON FIRST AUTHENTICATED LOAD, DESTRUCTIVELY. The first
// `setCurrentUser(id)` called with a real id, for each of localStorage and
// sessionStorage, walks every key currently in that area. A key already
// shaped like `ws:<id>:<key>` is left alone. A key matching a registered
// device-scoped exemption is left alone. Everything else is a legacy,
// un-namespaced key: its value is copied to `ws:<id>:<key>` and the old key is
// deleted. A one-time flag stops it from re-scanning on every later login.
//
// This is a MOVE, not a copy: if a second account signed in later also
// inherited a copy, the migration would have reproduced the exact defect it
// exists to close. A move means exactly one account, whichever signs in first
// after the host adopts this module, receives the pre-existing data, and
// every account after that starts clean.

/** The signed-in user's id, or null when no one is (or isn't known yet). */
export type UserId = number | string | null;

/** A key exempt from user-namespacing, with the reason it stays global. */
export type StorageExemption = { match: (key: string) => boolean; reason: string };

/** The one-time migration bookkeeping flag. Not a data key, never routed
 *  through `namespacedKey`, and always exempt regardless of the registered
 *  exemption list. */
const MIGRATION_DONE_KEY = "ws:legacy-migrated";

let deviceScoped: StorageExemption[] = [];

/** Register keys that stay global rather than being namespaced to the active
 *  user (a device identity, a public cache keyed by content rather than by
 *  viewer, a guard describing this tab's load). Additive: call it once per
 *  owner of a set of exemptions, each with its own reason. */
export function registerDeviceScoped(exemptions: StorageExemption[]): void {
  deviceScoped = deviceScoped.concat(exemptions);
}

/** Test-only: drop every registered exemption. */
export function _resetDeviceScopedForTest(): void {
  deviceScoped = [];
}

/** Pure: is this logical key exempt from namespacing? */
export function isDeviceScoped(key: string): boolean {
  return key === MIGRATION_DONE_KEY || deviceScoped.some((e) => e.match(key));
}

/** Pure: the real storage key for a logical key under a given user (or the
 *  pre-auth/anon bucket). `ws:<user_id>:<key>`. */
export function namespacedKey(userId: UserId, key: string): string {
  const id = userId === null || userId === undefined ? "anon" : String(userId);
  return `ws:${id}:${key}`;
}

/** Pure: does this RAW stored key already look like our namespaced shape?
 *  Requires two colons (`ws:`, an id, then the rest) so a one-colon sentinel
 *  like the migration flag is never mistaken for an already-migrated key. */
export function isNamespacedKey(rawKey: string): boolean {
  return /^ws:[^:]+:/.test(rawKey);
}

/** Pure: given every raw key currently in a storage area, decide which ones
 *  are legacy (pre-date namespacing) and where each one moves to. Skips
 *  anything already namespaced and anything device-scoped. Exported so the
 *  decision is testable without a real Storage. */
export function planMigration(
  rawKeys: string[],
  userId: number | string,
  isExempt: (key: string) => boolean = isDeviceScoped,
): { from: string; to: string }[] {
  const plan: { from: string; to: string }[] = [];
  for (const key of rawKeys) {
    if (isExempt(key) || isNamespacedKey(key)) continue;
    plan.push({ from: key, to: namespacedKey(userId, key) });
  }
  return plan;
}

// ---------------------------------------------------------------- impure

let currentUserId: UserId = null;

function snapshotKeys(area: Storage): string[] {
  const keys: string[] = [];
  for (let i = 0; i < area.length; i++) {
    const k = area.key(i);
    if (k !== null) keys.push(k);
  }
  return keys;
}

function migrateArea(area: Storage, userId: number | string): void {
  try {
    if (area.getItem(MIGRATION_DONE_KEY) === "1") return;
  } catch {
    return; // storage unreadable (private mode): nothing to migrate into
  }
  const plan = planMigration(snapshotKeys(area), userId);
  for (const { from, to } of plan) {
    try {
      const v = area.getItem(from);
      // Never overwrite a value already sitting at the destination (this
      // account may have already been migrated by an earlier tab).
      if (v !== null && area.getItem(to) === null) area.setItem(to, v);
      area.removeItem(from);
    } catch {
      /* best-effort per key; a stuck legacy key is not worse than today */
    }
  }
  try {
    area.setItem(MIGRATION_DONE_KEY, "1");
  } catch {
    /* private mode / quota, migration will just be retried next load */
  }
}

/** Called once by the host's auth boundary whenever the resolved user
 *  changes: after the initial "who am I" fetch, after login/register, and on
 *  logout (with `null`). A real id runs the one-time legacy migration for
 *  both storage areas. */
export function setCurrentUser(id: UserId): void {
  currentUserId = id;
  if (id === null) return;
  try {
    if (typeof localStorage !== "undefined") migrateArea(localStorage, id);
  } catch {
    /* private mode / disabled storage */
  }
  try {
    if (typeof sessionStorage !== "undefined") migrateArea(sessionStorage, id);
  } catch {
    /* private mode / disabled storage */
  }
}

/** Test-only: reset the module's notion of who is signed in. */
export function _resetCurrentUserForTest(): void {
  currentUserId = null;
}

// LOOKED UP LAZILY, AND THROUGH `globalThis`. A bare `localStorage` is a global
// REFERENCE, so in an environment that has none (server rendering) evaluating
// it throws a ReferenceError before any try/catch around the call can help.
// Reading the property off `globalThis` yields undefined instead, which every
// function below already handles.
function area(kind: "local" | "session"): Storage | null {
  try {
    const g = globalThis as { localStorage?: Storage; sessionStorage?: Storage };
    return (kind === "local" ? g.localStorage : g.sessionStorage) ?? null;
  } catch {
    // A cookies-off policy can make the property itself throw on access.
    return null;
  }
}

function read(area: Storage | null, key: string): string | null {
  try {
    return area?.getItem(isDeviceScoped(key) ? key : namespacedKey(currentUserId, key)) ?? null;
  } catch {
    return null;
  }
}

function write(area: Storage | null, key: string, value: string): void {
  try {
    area?.setItem(isDeviceScoped(key) ? key : namespacedKey(currentUserId, key), value);
  } catch {
    /* quota / private mode, caller already treats this as best-effort */
  }
}

function remove(area: Storage | null, key: string): void {
  try {
    area?.removeItem(isDeviceScoped(key) ? key : namespacedKey(currentUserId, key));
  } catch {
    /* nothing to do */
  }
}

const PROBE_KEY = "ws:probe";

/** Whether this storage area actually works right now, a probe write/remove
 *  rather than a lookup, because a blocked area (fully private browsing, a
 *  cookies-off policy) commonly still answers `getItem` with a harmless
 *  `null` and only throws on write. A caller that must tell "blocked" apart
 *  from "not set yet" checks this first. */
function available(area: Storage | null): boolean {
  if (!area) return false;
  try {
    area.setItem(PROBE_KEY, "1");
    area.removeItem(PROBE_KEY);
    return true;
  } catch {
    return false;
  }
}

/** The one storage surface an app should use in place of the native
 *  `localStorage`/`sessionStorage`. Mirrors their shape (`getItem`/
 *  `setItem`/`removeItem`) plus `available()`, so a call site swaps
 *  `localStorage.` for `userStorage.local.` (or `sessionStorage.` for
 *  `userStorage.session.`) and nothing else about it changes. Every key is
 *  namespaced to the active user (see `setCurrentUser`) unless it has been
 *  registered as device-scoped (see `registerDeviceScoped`). */
export const userStorage = {
  local: {
    getItem: (key: string) => read(area("local"), key),
    setItem: (key: string, value: string) => write(area("local"), key, value),
    removeItem: (key: string) => remove(area("local"), key),
    available: () => available(area("local")),
  },
  session: {
    getItem: (key: string) => read(area("session"), key),
    setItem: (key: string, value: string) => write(area("session"), key, value),
    removeItem: (key: string) => remove(area("session"), key),
    available: () => available(area("session")),
  },
};
