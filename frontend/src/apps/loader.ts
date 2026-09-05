// Fetching one app's bundle, and never throwing while doing it.
//
// The rule `lib/lazyPage.tsx` states for the shell's own chunks -- A PAGE THAT
// FAILS TO LOAD MUST NOT BE A BLANK PAGE -- extended to a bundle the platform
// did not compile. `loadApp` resolves a discriminated union for every outcome,
// including the ones that are ordinary bugs in an app, so a failure is a
// message inside one region rather than an absence everywhere.
//
// The stylesheet is linked and AWAITED before the module is imported. Importing
// first paints one frame unstyled, and a foreign bundle gets no preload helper
// from Vite (it is not in the shell's rollup graph), so the shell linking the
// sheet by name is the only thing that loads it at all.
import type { ComponentType } from "react";
import {
  APP_UI_CONTRACT,
  type AppDeclaration,
  type AppLoadFailure,
  type AppLoadResult,
  type WorkshopAppBundle,
} from "./contract";

/** One resolved screen, or why there is not one. */
export type AppExportResult = { ok: true; Component: ComponentType } | AppLoadFailure;

// The API root as every other module in this app spells it. The backend returns
// URLs relative to its own root because the prefix belongs to the edge, not to
// the backend; this is the one place the loader adds it.
const API_BASE = "/api";

/** Memoized per resolved entry URL, not per app id: the URL carries a hash of
 *  the app's own bytes, so a rebuilt app is a different key and reloads, while
 *  navigating back and forth between two of its pages does not refetch. */
const inFlight = new Map<string, Promise<AppLoadResult>>();

function absolute(url: string): string {
  return url.startsWith("/") ? API_BASE + url : url;
}

/** Link one stylesheet and resolve when the browser has applied it.
 *
 *  Resolves rather than rejects on error: a missing stylesheet is a degraded
 *  app, not an absent one, and the module is still worth loading. The bundle's
 *  own failure is what the caller reports. */
function linkStyle(href: string): Promise<void> {
  const existing = document.querySelector<HTMLLinkElement>(
    `link[rel="stylesheet"][data-app-style="${CSS.escape(href)}"]`,
  );
  if (existing) return Promise.resolve();
  return new Promise((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.appStyle = href;
    link.addEventListener("load", () => resolve(), { once: true });
    link.addEventListener("error", () => resolve(), { once: true });
    document.head.appendChild(link);
  });
}

function isBundle(value: unknown): value is WorkshopAppBundle {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<WorkshopAppBundle>;
  return typeof candidate.contract === "number"
    && !!candidate.exports
    && typeof candidate.exports === "object";
}

async function fetchBundle(app: AppDeclaration, entry: string): Promise<AppLoadResult> {
  for (const href of app.urls?.styles ?? []) {
    await linkStyle(absolute(href));
  }
  let mod: { default?: unknown };
  try {
    // `@vite-ignore` is required: without it rollup tries to analyze a runtime
    // URL, warns, and in some configurations rewrites it.
    mod = (await import(/* @vite-ignore */ absolute(entry))) as { default?: unknown };
  } catch (e) {
    return {
      ok: false,
      reason: "This app's code could not be loaded.",
      detail: `${absolute(entry)} (${e instanceof Error ? e.message : String(e)})`,
    };
  }
  const bundle = mod.default;
  if (!isBundle(bundle)) {
    return {
      ok: false,
      reason: "This app's bundle does not export what the platform expects.",
      detail: "Its entry must default-export { contract, exports }.",
    };
  }
  if (bundle.contract !== APP_UI_CONTRACT) {
    return {
      ok: false,
      reason: "This app was built for a different version of the platform.",
      detail: `Its bundle says contract ${bundle.contract} and this shell implements ${APP_UI_CONTRACT}.`,
    };
  }
  return { ok: true, bundle };
}

/** Load an app's bundle. Resolves; never rejects.
 *
 *  The declaration's own `error` is checked FIRST and refuses without a fetch:
 *  a contract or kit mismatch is known from the manifest, and a version check
 *  that arrives after the bundle has loaded has checked nothing. */
export function loadApp(app: AppDeclaration): Promise<AppLoadResult> {
  if (app.error) {
    return Promise.resolve({ ok: false, reason: "This app cannot be loaded.", detail: app.error });
  }
  const entry = app.urls?.entry;
  if (!entry) {
    return Promise.resolve({
      ok: false,
      reason: "This app declares screens and ships no bundle.",
      detail: "Its manifest names a ui.entry that is not built.",
    });
  }
  const cached = inFlight.get(entry);
  if (cached) return cached;
  const promise = fetchBundle(app, entry).then((result) => {
    // A failure is not cached: the usual cause is a stale hash after the app
    // was rebuilt while this tab was open, and the retry is with a new URL.
    if (!result.ok) inFlight.delete(entry);
    return result;
  });
  inFlight.set(entry, promise);
  return promise;
}

/** One named export from a loaded bundle, or a failure naming what is missing.
 *
 *  Separate from `loadApp` because a manifest naming an export the bundle does
 *  not have is a different mistake from a bundle that will not load, and the
 *  two need different sentences. */
export function bundleExport(result: AppLoadResult, exportName: string): AppExportResult {
  if (!result.ok) return result;
  const Component = result.bundle.exports[exportName];
  if (!Component) {
    return {
      ok: false,
      reason: "This app's screen is missing from its bundle.",
      detail: `Its manifest names the export ${exportName} and the bundle provides ${
        Object.keys(result.bundle.exports).join(", ") || "nothing"
      }.`,
    };
  }
  return { ok: true, Component };
}

/** Drop everything remembered about loaded bundles. Used when the declarations
 *  are refetched, so an app that was rebuilt is re-imported rather than served
 *  from the memo under its old hash. */
export function forgetLoadedApps(): void {
  inFlight.clear();
}
