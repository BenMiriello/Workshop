// The app catalog for the frontend shell.
//
// THIS FILE NAMES NO APP. It used to be the "one file allowed to name a
// specific app": a literal `APPS` array holding every app's routes, crumbs,
// icon, provider and launcher probe. So the module that assembles the catalog
// also decided which apps exist, and adding or removing one was a platform
// edit -- the counterpart of the backend's `_CATALOG_FACTORIES` tuple, removed
// the same way on 2026-09-01.
//
// An app declares itself in `pages/apps/<app>/module.tsx`, exporting
// `appModule: AppModule`, and this file finds those by scanning. Everything
// else in the shell -- App.tsx's route table, appNav's breadcrumbs and app
// switcher, the launcher's status probes -- reads the functions below, so
// nothing else lists apps either.
//
// The contract is `appModule.ts` (types) and `appShell.tsx` (the two pieces an
// app needs from the shell to build its routes), kept apart from this file so
// an app module can import what it fills in without importing the discoverer.
//
// Pages stay lazily imported INSIDE each app's module: an instance without an
// app should not pay for its bundle. For an app that has moved to the RUNTIME
// loader that is no longer the mechanism at all -- its bundle is fetched by URL
// when one of its declared paths is first opened, and the shell's build never
// contains it. See declarations.tsx and loader.ts.

import { Suspense } from "react";
import type { ComponentType, ReactNode } from "react";
import { RouteBoundary, RouteSkeleton } from "../lib/lazyPage";
import type { AppModule, AppStatus, CrumbNode } from "./appModule";

export type { AppModule, AppStatus, CrumbNode };

// DISCOVERY. `import.meta.glob` is resolved by Vite at build time, so this is
// still a static list as far as bundling is concerned -- each app's module is a
// real dependency and its pages still code-split behind their own lazy imports.
// What it is NOT is a list anyone maintains: an app joins the shell by having a
// `module.tsx` and leaves by not having one.
//
// `eager` because the shell needs the route table synchronously to render at
// all. The modules themselves are tiny -- every page inside them is lazy -- so
// eager costs the declarations and nothing else.
const IN_TREE = import.meta.glob<{ appModule?: AppModule }>(
  "../pages/apps/*/module.tsx",
  { eager: true },
);

// EXTRACTED apps: `apps/<slug>/ui/module.tsx`, outside this package entirely.
// `apps/` is the operator's own overlay and is untracked by this repo, so on a
// fresh clone this pattern matches NOTHING and the shell builds with an empty
// catalog -- which is exactly what `check_fresh_clone.sh` asserts, now measured
// by the build itself rather than by a separate scan.
//
// This is the BUILD-TIME half of the loader, and it is now one of two. It gets
// an app's source out of the platform repo, which was the requirement, but a
// glob is resolved when the shell is compiled, so on its own it still means a
// rebuild per install. The runtime half is below: `declarations.tsx` reads the
// platform's own roster over HTTP and an app arrives with no rebuild at all.
// Both paths produce the same `AppModule`, deliberately, so an app can move
// from one to the other by being built rather than by being rewritten.
const EXTRACTED = import.meta.glob<{ appModule?: AppModule }>(
  "../../../apps/*/ui/module.tsx",
  { eager: true },
);

// An extracted app SHADOWS an in-tree one of the same id, the same way an
// installed distribution shadows an in-tree declaration in the backend
// (`app_registry._build_catalog`). Extraction is a sequence, not a flag day:
// while it runs, some apps are here and some are there, and the moment an app's
// files land in `apps/` it is the one that counts -- so a half-deleted in-tree
// copy can never win and render stale code.
function declared(mods: Record<string, { appModule?: AppModule }>): AppModule[] {
  return Object.values(mods)
    .map((m) => m?.appModule)
    .filter((a): a is AppModule => Boolean(a && a.id));
}

// THE THIRD SOURCE, and the one that does not need a rebuild: apps the PLATFORM
// says are installed, read over HTTP from `/base-apps/contributions` and turned
// into these same declarations by `declarations.tsx`. The two globs above are
// resolved when the shell is compiled, so with only them, installing an app is
// a rebuild of the shell and removing one is another. A runtime declaration is
// none of that: it arrives with the roster.
//
// A HEALTHY runtime declaration SHADOWS a build-time module of the same id, the
// same way an installed distribution shadows an in-tree declaration in the
// backend. `declarations.canActivate` is what "healthy" means, and it is why
// this is a sequence rather than a flag day: an app whose bundle is not built
// yet is not published at all, so it keeps rendering from the shell's own
// build, and it flips to its own bundle the moment that bundle exists.
//
// Sorted by the `order` each app declares, then by id. Discovery order is
// filesystem order, and the switcher's siblings read in a deliberate order that
// alphabetising would silently change; `order` is how an app states its place
// without this file knowing what that place is.
const BUNDLED: AppModule[] = [...declared(IN_TREE), ...declared(EXTRACTED)];

// PUSHED IN by `declarations.tsx`, never pulled from it. This file must not
// import that one: the loader's route host reaches the shell for its group nav
// and chat dock, so pulling would close a cycle THROUGH this module's own
// initialization, and the first thing this module does at init is build the
// catalog. Measured 2026-09-05, as "declarationState is not a function" from a
// test that imported the declarations first.
const DECLARED: AppModule[] = [];

function catalog(): AppModule[] {
  const byId: Record<string, AppModule> = {};
  for (const a of BUNDLED) byId[a.id] = a;
  for (const a of DECLARED) byId[a.id] = a;
  return Object.values(byId).sort(
    (x, y) =>
      (x.order ?? Number.MAX_SAFE_INTEGER) - (y.order ?? Number.MAX_SAFE_INTEGER) ||
      x.id.localeCompare(y.id),
  );
}

// MUTATED IN PLACE rather than reassigned, so every module that already holds
// this array (and there are several) sees the same list the moment the
// declarations land, with no import-binding subtleties to reason about.
export const APPS: AppModule[] = catalog();

const changeListeners = new Set<() => void>();

/** Run `fn` whenever the catalog changes, which is when the platform's app
 *  roster is read or re-read. For the few consumers that copy the catalog into
 *  their own structure at module init (appNav's breadcrumb nodes) rather than
 *  reading it during render. */
export function onAppsChanged(fn: () => void): () => void {
  changeListeners.add(fn);
  return () => changeListeners.delete(fn);
}

/** Replace the runtime-declared half of the catalog. Called by
 *  `declarations.tsx` every time the platform's app roster is read. */
export function setDeclaredApps(apps: AppModule[]): void {
  DECLARED.splice(0, DECLARED.length, ...apps);
  APPS.splice(0, APPS.length, ...catalog());
  for (const fn of changeListeners) fn();
}

/** Every app's <Route> elements, for the shell's one <Routes>. */
export function appRoutes(): ReactNode[] {
  return APPS.map((a) => a.routes());
}

/** Breadcrumb nodes from every app, merged. */
export function appCrumbs(): Record<string, CrumbNode> {
  return Object.assign({}, ...APPS.map((a) => a.crumbs));
}

/** Legacy `?tab=` values from retired platform pages, mapped to where the app
 *  that absorbed each one now lives. Merged from the apps' own declarations. */
export function appLegacyTabs(): Record<string, string> {
  return Object.assign({}, ...APPS.map((a) => a.legacyTabs ?? {}));
}

/** Old top-level paths apps absorbed, merged from their own declarations.
 *  `App.tsx` renders one <Navigate replace> per entry and names no app. */
export function appRedirects(): Record<string, string> {
  return Object.assign({}, ...APPS.map((a) => a.redirects ?? {}));
}

/** The switcher groups: sibling apps that link to each other. */
export function appGroups(): { label: string; to: string }[][] {
  const byGroup = new Map<string, { label: string; to: string }[]>();
  for (const a of APPS) {
    if (!a.group || !a.link) continue;
    const list = byGroup.get(a.group) ?? [];
    list.push(a.link);
    byGroup.set(a.group, list);
  }
  return [...byGroup.values()].filter((g) => g.length > 1);
}

/** Mounts every app provider around the shell, outermost first. */
export function AppProviders({ children }: { children: ReactNode }) {
  return APPS.reduce<ReactNode>(
    (acc, a) => (a.Provider ? <a.Provider>{acc}</a.Provider> : acc),
    children,
  ) as React.ReactElement;
}

/** Wraps lazily-loaded app pages. The shell renders instantly either way.
 *
 *  The boundary is not optional. `fallback={null}` with nothing catching the
 *  load rejection meant a chunk that 404s (a tab open across a rebuild, which
 *  renames every content-hashed chunk) left the route blank permanently, with
 *  no message and no way back. See lib/lazyPage. */
export function AppSuspense({ children }: { children: ReactNode }) {
  return (
    <RouteBoundary>
      <Suspense fallback={<RouteSkeleton />}>{children}</Suspense>
    </RouteBoundary>
  );
}

/** The Settings panel this app declares, if it declares one.
 *
 *  It used to be `appSettingsPanels()`, returning only the panels and in
 *  catalog order, which made Settings > Apps a list of PANELS rather than a
 *  list of apps: one app declares a panel, so the page was that one app. The
 *  caller now walks the platform's installed-app list and asks this per app,
 *  so an app with no panel is a row that says so instead of a row that is
 *  missing. */
export function appSettingsPanel(id: string): ComponentType | undefined {
  return APPS.find((a) => a.id === id)?.Settings;
}

/** The icon an app declared, if it declared one. A token/url string, never a
 *  component: resolving it is lib/appIcon's job, and the fallback is a monogram
 *  of the app's name rather than a generic placeholder glyph. */
export function appIcon(id: string): string | undefined {
  return APPS.find((a) => a.id === id)?.icon;
}

/** Every app that has a screen to open, as the shell's dock needs it.
 *
 *  An app with no routes (health, whose entire frontend is a Settings panel)
 *  has nowhere to land, so it gets no dock tile. That is a normal case, not a
 *  special one — same reasoning as `routes: () => null` being allowed.
 *
 *  The landing path is the app's `link`, or its shallowest breadcrumb node.
 *  Nothing new has to be declared per app for the dock to work, which is the
 *  point: the shell must not become a second place that lists apps. */
export function appPlaces(): {
  id: string;
  name: string;
  home: string;
  icon?: string;
  landing: string;
  ownScreen: true;
}[] {
  const out: ReturnType<typeof appPlaces> = [];
  for (const a of APPS) {
    const paths = Object.keys(a.crumbs).sort((x, y) => x.length - y.length);
    const home = a.link?.to ?? paths[0];
    if (!home) continue;
    out.push({
      id: a.id,
      name: a.link?.label ?? a.crumbs[home]?.label ?? a.id,
      home,
      icon: a.icon,
      // An app in THIS list ships its own screens, so its home is where it
      // opens. That is the declaration this file has always carried; it just
      // had no name until the platform grew one for apps that make it
      // elsewhere (backend/services/app_surface.py).
      landing: home,
      ownScreen: true,
    });
  }
  return out;
}

/** Load every installed app's launcher status, keyed by app id. */
export async function appStatuses(): Promise<Record<string, AppStatus>> {
  const out: Record<string, AppStatus> = {};
  await Promise.all(
    APPS.filter((a) => a.status).map(async (a) => {
      out[a.id] = await a.status!();
    }),
  );
  return out;
}
