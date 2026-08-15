// Developer mode — the frontend half. ADR 0014.
//
// Developer mode is ONE question: is the platform repo permitted as a run
// target? On the backend that is a directory comparison
// (`app_runner.is_self_dir`); here it is a BUILD-TIME flag, because the useful
// property on this side is stronger than a check. In a normal build the
// Development scope and the surfaces that reach it are not in the bundle at
// all, and absent code needs no permission system — which is how "a regular
// user cannot edit the Workshop" is satisfied without adding the multi-tenant
// guardrails this repo forbids.
//
// THIS IS A PRODUCT CAPABILITY BOUNDARY AND A SAFETY INTERLOCK, NOT A SECURITY
// BOUNDARY. Anyone with a shell on the box can still edit the source; real
// confinement is a separate execution identity ([SEC-A1], open). No copy in this
// UI may imply confinement.
//
// Read in exactly one place — here — mirroring `WORKSHOP_DEV_MODE` in
// backend/services/app_runner.py and `dev_mode_on` in scripts/dev_mode_gate.sh.
// Follows the existing build-time-flag pattern (`VITE_PUBLIC` in BirdsShell).
//
// Every rule below takes `devMode` as an argument defaulting to DEV_MODE. That
// is what makes the absence testable: a module-level constant is frozen at
// import, so a test could never exercise the off case that matters most.
//
// The DEFAULTS, which are the thing an actual build freezes, are checked by
// `bash scripts/check_dev_bundle.sh` (ADR 0014 phase 6). It compiles
// devMode.bundleProbe.ts with VITE_DEV_MODE stripped from frontend/.env and runs
// what comes out. It is not in `npm test`: it has to write frontend/.env, and a
// suite that mutates deployment config is one nobody can run twice. Run it after
// changing anything here or in chatScope.ts.

import { DEVELOPMENT_SCOPE, devScopes as _devScopes, orderScopes as _orderScopes } from "./scope";

export const DEV_MODE = import.meta.env.VITE_DEV_MODE === "1";

// The one name that resolves to the platform repo. There used to be five.
// Re-exported from `./scope`, the single owner of the scope model
// (memory-and-documentation-model-2026-08-14.md sec 7.1); kept here too so
// every existing import of it from this module keeps working unchanged.
export { DEVELOPMENT_SCOPE };

// Scope keys that exist only in a developer build. Empty otherwise — the scope
// is absent, not present-and-disabled, because a disabled control still tells
// every user about a capability they do not have and cannot get.
export function devScopes(devMode: boolean = DEV_MODE): string[] {
  return _devScopes(devMode);
}

// Order a list of scope keys for a picker, and drop dev-only scopes from a
// non-dev build.
//
// Development sorts ABOVE Workshop. Workshop is the widest scope and normally
// leads, but Development is where a developer already is, and putting the root
// organising layer above it would make the common case the second entry. The
// server orders `GET /api/tasks/scopes` the same way (`_scope_roots`); this is
// the client-side half of the same rule, applied to whatever the server sent so
// a non-dev bundle talking to a dev backend still shows a non-dev list.
export function orderScopes(scopes: string[], devMode: boolean = DEV_MODE): string[] {
  return _orderScopes(scopes, devMode);
}
