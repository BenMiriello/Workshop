// ONE SHARED RUNTIME PER DOCUMENT, asserted at the moment a second one loads.
//
// An app's bundle is fetched by URL and resolves `react` (and the twelve other
// specifiers in `@workshop/app-build`'s SHARED) through the import map in
// index.html. When that map is right there is exactly one URL per specifier and
// therefore exactly one module instance. When it is wrong -- a map entry
// pointing into `/node_modules/`, whose dev URL carries a re-optimization token
// that changes; an app built without the preset, so React went INTO its bundle
// -- the browser evaluates a second copy with identical bytes, and every hook
// in that app throws "Invalid hook call", which names a symptom four layers
// away from the mistake.
//
// This module is imported by every re-export module beside it AND by the
// shell's own entry, so the shell registers its React before any app can load.
// A module body runs once per URL, so a second copy arriving under any other
// URL fires this immediately, in dev and in production, with a sentence naming
// the cause.
import * as React from "react";

const KEY = "__workshop_shared_runtime__";

// The identity of THIS copy of React. `Children` is an ordinary object created
// in React's own module scope, so two copies never share one. Deliberately not
// `Fragment`: that is `Symbol.for("react.fragment")`, and a registered symbol
// IS shared across copies, which would make this check silently always pass.
const mine: unknown = React.Children;

const g = globalThis as unknown as Record<string, unknown>;
const already = g[KEY];

if (already !== undefined && already !== mine) {
  throw new Error(
    "Workshop: two copies of the shared runtime are loaded in this document. " +
      "An app bundle resolved react outside the import map, so its hooks cannot " +
      "work. Check the importmap in index.html and the externals in " +
      "@workshop/app-build.",
  );
}

g[KEY] = mine;

export {};
