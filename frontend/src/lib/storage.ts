// The ONE module that touches `localStorage` and `sessionStorage`. ADR 0025
// clause 4: "Every localStorage and sessionStorage key is prefixed with the
// acting user's id: ws:<user_id>:<key>." That is the whole convention; nothing
// here invents a different one.
//
// WHY THIS EXISTS. Every key in this app used to be global to the browser
// profile. A second signed-in user on the same machine inherited the first
// user's chat drafts, unsent outbox, scroll positions and device tokens — a
// present-tense defect, not a future multi-user concern. `check_no_raw_storage.py`
// enforces the other half: nothing outside this module may call the native
// `localStorage`/`sessionStorage` API at all, so an exemption from namespacing
// is a diff someone reviews (see DEVICE_SCOPED below) rather than a silent
// omission.
//
// THE BOOTSTRAP PROBLEM, ANSWERED. A read that happens before the signed-in
// user is known cannot be told apart from a read for the wrong user, so the
// question is: can that actually happen here?
//
//   `src/main.tsx` mounts `<AuthProvider><Guard><App/></Guard></AuthProvider>`.
//   `Guard` (src/auth/Guard.tsx) renders NOTHING of `<App/>` — not a stale
//   frame, not a skeleton with the real tree underneath — until `useAuth().status`
//   has left "loading". While loading it shows a brand splash; if there is no
//   session it shows `<Login/>` instead of `<App/>`. Every page that reads or
//   writes storage (Chat, the birds recorder, Finance, AgentBuild, ...) is a
//   descendant of `<App/>`, and every one of those is itself lazy-mounted
//   behind its own route — so its first render, and therefore the first
//   `useState(() => storage.getItem(...))` initializer it runs, cannot happen
//   before Guard has already resolved `status` to "authed". `components/chat/
//   threadCache.ts` was flagged as a second early reader; reading it confirms
//   it is a module-level, in-memory `Map` that never calls a storage API at
//   all (its header says so on purpose), so it has nothing to bootstrap.
//
//   So in this app's actual render tree, a storage call with no signed-in user
//   should never happen. This module does not trust that from the outside —
//   a future page could render outside `<Guard>`, a test could call a hook
//   directly, this file could be reused somewhere the gate does not apply —
//   so `setCurrentUser` defaults to `null` and every call made before the
//   first `setCurrentUser(id)` reads and writes under the literal bucket
//   `ws:anon:<key>`. That bucket can never collide with a real account's data
//   (a real user id never stringifies to "anon"), so the failure mode of an
//   unreachable-in-practice early call is "this key round-trips inside its own
//   quarantined bucket," never "this key leaks into someone's account."
//
// THE PRE-EXISTING-KEYS PROBLEM, ANSWERED. Every key already sitting in a
// browser profile from before this module existed has no owner recorded — the
// defect this ADR closes is exactly that none of them ever did. There is no
// signal in a legacy key that says which account it belongs to. Silently
// orphaning them is not acceptable either: the operator is using this app
// right now, and losing an in-progress chat draft or an unsent outbox entry
// over a storage-key rename is a real regression, not a theoretical one.
//
// So: MIGRATE ONCE, ON FIRST AUTHENTICATED LOAD, DESTRUCTIVELY. The first
// `setCurrentUser(id)` called with a real id, for each of localStorage and
// sessionStorage, walks every key currently in that area. A key already
// shaped like `ws:<id>:<key>` is left alone (already migrated, or written
// under this scheme already). A key matching a DEVICE_SCOPED entry is left
// alone (see below). Everything else is a legacy, un-namespaced key from
// before this module existed: its value is copied to `ws:<id>:<key>` and the
// old key is deleted. A one-time flag (itself exempt — see MIGRATION_DONE_KEY)
// stops it from re-scanning on every later login.
//
// This is a MOVE, not a copy, and that is load-bearing: if a second account
// signed in later also inherited a copy, this module would have reproduced
// the exact defect it exists to close. A move means exactly one account —
// whichever signs in first after this ships — receives the pre-existing data,
// and every account after that starts clean. There is no way to do better
// than "whichever signs in first" without a signal the legacy keys never
// recorded, and it is not worse than the status quo: today ANY concurrent
// user already reads and clobbers those same un-namespaced keys, on every
// load, forever. Turning that into "the first person to open the app after
// the deploy keeps them, once" is strictly narrower. In the system as it
// actually exists today there is one operator, so this is exactly "restore
// the operator's drafts," which is the case that matters.
//
// DEVICE-SCOPED EXEMPTIONS. ADR 0025 clause 4: "Keys that genuinely describe
// the device rather than the person ... may stay global, but only by being
// listed in the namespacing module with a reason." Each entry below is that
// diff. Everything not listed here defaults to namespaced — that default is
// the point, so a new key someone adds later is safe by construction and has
// to opt out on purpose, not opt in.

/** The signed-in user's id, or null when no one is (or isn't known yet — see
 *  the bootstrap note above). Matches `AuthUser["id"]` from `lib/auth.ts`. */
export type UserId = number | null;

/** The one-time migration bookkeeping flag. Not a data key — never routed
 *  through `namespacedKey`, never migrated itself. */
const MIGRATION_DONE_KEY = "ws:legacy-migrated";

type Exemption = { match: (key: string) => boolean; reason: string };

const DEVICE_SCOPED: Exemption[] = [
  {
    match: (k) => k === MIGRATION_DONE_KEY,
    reason: "the migration bookkeeping flag itself, not a data key",
  },
  {
    match: (k) => k === "ws-chunk-reloaded",
    reason:
      "guards a one-shot reload after a stale JS chunk 404s (lib/lazyPage.tsx); " +
      "describes this tab's load, not the signed-in account",
  },
  {
    match: (k) => k.startsWith("birdinfo:"),
    reason:
      "public species reference data from iNaturalist/Wikipedia " +
      "(pages/apps/birds/birdinfo.ts), identical for every viewer and keyed only " +
      "by scientific name — it carries no user-specific content to leak",
  },
  {
    match: (k) =>
      [
        "birdDeviceToken", "birdDeviceName", "birdDeviceMode",
        "birdDeviceFixedLoc", "birdDeviceShared",
        // pre-rename legacy names the recorder already migrates on its own
        "birdStationToken", "birdStationLabel",
        // this device's live-recording session mirror, tied to the device
        // identity above, not to whoever is looking at the page
        "birdSessionSpecies", "birdSessionLog", "birdWasListening",
      ].includes(k),
    reason:
      "identity and live-recording state of THIS physical recording device " +
      "(pages/apps/birds/recorder.tsx), independent of which web account is " +
      "viewing it — the file's own header calls the token 'the only durable " +
      "client identity' and treats the server, not the browser, as the source " +
      "of truth for who is looking at it",
  },
];

/** Pure: is this logical key exempt from namespacing? */
export function isDeviceScoped(key: string): boolean {
  return DEVICE_SCOPED.some((e) => e.match(key));
}

/** Pure: the real storage key for a logical key under a given user (or the
 *  pre-auth/anon bucket). `ws:<user_id>:<key>`, exactly ADR 0025 clause 4. */
export function namespacedKey(userId: UserId, key: string): string {
  const id = userId === null || userId === undefined ? "anon" : String(userId);
  return `ws:${id}:${key}`;
}

/** Pure: does this RAW stored key already look like our namespaced shape?
 *  Requires two colons (`ws:`, an id, then the rest) so the one-colon
 *  `MIGRATION_DONE_KEY` sentinel is never mistaken for an already-migrated
 *  data key. */
export function isNamespacedKey(rawKey: string): boolean {
  return /^ws:[^:]+:/.test(rawKey);
}

/** Pure: given every raw key currently in a storage area, decide which ones
 *  are legacy (pre-dates this module) and where each one moves to. Skips
 *  anything already namespaced and anything device-scoped. Exported so the
 *  decision is testable without a real Storage. */
export function planMigration(
  rawKeys: string[],
  userId: number,
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

function migrateArea(area: Storage, userId: number): void {
  try {
    if (area.getItem(MIGRATION_DONE_KEY) === "1") return;
  } catch {
    return; // storage unreadable (private mode) — nothing to migrate into
  }
  const plan = planMigration(snapshotKeys(area), userId);
  for (const { from, to } of plan) {
    try {
      const v = area.getItem(from);
      // Never overwrite a value already sitting at the destination — this
      // account may have already been migrated by an earlier tab.
      if (v !== null && area.getItem(to) === null) area.setItem(to, v);
      area.removeItem(from);
    } catch {
      /* best-effort per key; a stuck legacy key is not worse than today */
    }
  }
  try {
    area.setItem(MIGRATION_DONE_KEY, "1");
  } catch {
    /* private mode / quota — migration will just be retried next load */
  }
}

/** Called once by `AuthProvider` whenever the resolved user changes: after
 *  `fetchMe()` on boot, after login/register, and on logout (with `null`).
 *  A real id runs the one-time legacy migration for both storage areas. */
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
// REFERENCE, so in an environment that has none (server rendering, which this
// repo uses to smoke-test components without a DOM) evaluating it throws a
// ReferenceError before any try/catch around the call can help. Reading the
// property off `globalThis` yields undefined instead, which every function
// below already handles.
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
    /* quota / private mode — caller already treats this as best-effort */
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

/** Whether this storage area actually works right now — a probe write/remove,
 *  not a lookup, because a blocked area (fully private browsing, cookies-off
 *  policies) commonly still answers `getItem` with a harmless `null` and only
 *  throws on write. A caller that must tell "blocked" apart from "not set yet"
 *  (lib/lazyPage.tsx's reload guard, so it does not loop when it can't
 *  remember that it already reloaded) checks this first. */
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

/** The only surface the rest of the app is allowed to use. Mirrors the native
 *  `Storage` shape (`getItem`/`setItem`/`removeItem`) so a call site swaps
 *  `localStorage.` for `userStorage.local.` (or `sessionStorage.` for
 *  `userStorage.session.`) and nothing else about it changes. */
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
