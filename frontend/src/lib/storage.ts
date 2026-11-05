// Workshop's own instantiation of the generic namespaced-storage engine
// (`@workshop/ui`'s `storage.ts`, promoted there 2026-08-20 as part of
// removing app pages' reach into platform-internal frontend modules; see
// docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md
// "Stage 4b"). The engine (namespace-by-active-user, the one-time legacy-key
// migration, the exemption mechanism) is generic and lives in the kit; this
// file is the one place that says WHICH keys are exempt for Workshop, per
// ADR 0025 clause 4: "Keys that genuinely describe the device rather than the
// person ... may stay global, but only by being listed in the namespacing
// module with a reason."
//
// `check_no_raw_storage.py` enforces the other half of that ADR clause:
// nothing outside this module and the kit's `storage.ts` may call the native
// `localStorage`/`sessionStorage` API at all.
import {
  _resetCurrentUserForTest as resetCurrentUserOnKit, isDeviceScoped, isNamespacedKey,
  namespacedKey, planMigration, registerDeviceScoped, setCurrentUser as setCurrentUserOnKit,
  userStorage, type StorageExemption, type UserId,
} from "@workshop/ui";

registerDeviceScoped([
  {
    match: (k) => k === "ws-chunk-reloaded",
    reason:
      "guards a one-shot reload after a stale JS chunk 404s (lib/lazyPage.tsx); " +
      "describes this tab's load, not the signed-in account",
  },
] satisfies StorageExemption[]);

/** Called once by `AuthProvider` whenever the resolved user changes: after
 *  `fetchMe()` on boot, after login/register, and on logout (with `null`). */
export function setCurrentUser(id: UserId): void {
  setCurrentUserOnKit(id);
}

export { isDeviceScoped, isNamespacedKey, namespacedKey, planMigration, userStorage };
export type { UserId };

/** Test-only: reset the module's notion of who is signed in. */
export function _resetCurrentUserForTest(): void {
  resetCurrentUserOnKit();
}
