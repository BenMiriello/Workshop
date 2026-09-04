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
//
// WHY THE BIRD-SPECIFIC EXEMPTIONS STAY HERE, NOT IN THE BIRDS APP. Moving
// them to a birds-owned module (which app pages otherwise should own) would
// be correct in the end state, but it is unsafe today: `registerDeviceScoped`
// must run before the FIRST `setCurrentUser(id)` migration sweep, and this
// file is what `auth/AuthProvider.tsx` imports eagerly at boot, while the
// birds pages are lazy-loaded (`apps/registry.tsx`) and may not have run yet
// on a session's first login. Registering here, eagerly, is what guarantees
// the migration sweep never sees a bird key before it knows the key is
// exempt. This is a deliberate, temporary exception to "an app owns its own
// storage keys": it leaves with birds' content at stage 7 (extraction), when
// the app gets its own eagerly-loaded entry point that can register itself
// before anything reads app-agnostic storage.
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
  {
    match: (k) => k.startsWith("birdinfo:"),
    reason:
      "public reference data an app fetches from a third party, identical " +
      "for every viewer and keyed only by a public identifier, so it carries " +
      "no user-specific content to leak",
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
      "identity and live-recording state of THIS physical device, " +
      "independent of which web account is " +
      "viewing it: the file's own header calls the token 'the only durable " +
      "client identity' and treats the server, not the browser, as the source " +
      "of truth for who is looking at it",
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
