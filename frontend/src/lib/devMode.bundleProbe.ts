// The entry `scripts/check_dev_bundle.sh` compiles to answer ADR 0014 phase 6:
// are the Development surfaces present in a bundle built without VITE_DEV_MODE?
//
// Nothing in the app imports this file, and nothing should. It exists because
// the property under test is a BUILD-TIME one. `DEV_MODE` is frozen at import
// from `import.meta.env.VITE_DEV_MODE`, so the only way to observe the value a
// real build baked in is to compile a module through Vite with the flag in the
// state you want and then run what came out. `devMode.test.ts` passes `devMode`
// explicitly, which tests the rules and deliberately cannot test the defaults;
// this tests the defaults and nothing else.
//
// It is a tracked source file rather than something the checker generates so
// that `tsc --noEmit` covers it: rename or drop a gate and this stops compiling,
// instead of the check quietly going vacuous.
//
// Everything here reads a DEFAULT argument. Passing one would defeat the point.
import { DEV_MODE, DEVELOPMENT_SCOPE, devScopes, orderScopes } from "./devMode";
import { SCOPE_GROUPS, scopeGroups } from "../components/chat/chatScope";

export type BundledDevSurfaces = {
  /** The compiled value of the build-time flag. */
  devMode: boolean;
  /** Dev-only scope keys the bundle offers. */
  devScopes: string[];
  /** What the bundle does to a scope list a developer backend sent. */
  orderedServerScopes: string[];
  /** Chat scope picker groups, recomputed. */
  scopeGroupKinds: string[];
  /** Chat scope picker groups as the module froze them at import: what renders. */
  frozenScopeGroupKinds: string[];
};

export function bundledDevSurfaces(): BundledDevSurfaces {
  return {
    devMode: DEV_MODE,
    devScopes: devScopes(),
    orderedServerScopes: orderScopes([DEVELOPMENT_SCOPE, "workshop", "project:birds"]),
    scopeGroupKinds: scopeGroups().map((g) => String(g.kind)),
    frozenScopeGroupKinds: SCOPE_GROUPS.map((g) => String(g.kind)),
  };
}
