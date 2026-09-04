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
// app should not pay for its bundle, and until dynamic bundle injection exists
// (docs/apps-are-pluggable.md calls this the deferred seam) lazy imports are
// what keeps an uninstalled app's code off the critical path.

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
// This is the build-time half of the loader. It gets an app's SOURCE out of the
// platform repo, which is the requirement; it does not yet let an app be
// installed without rebuilding the shell, which is the runtime import-map
// loader in `stage-5-app-loader-plan-2026-08-24.md`. That plan is unchanged by
// this and lands on the same files: an app already lives at `apps/<slug>/ui`
// and already exports `appModule`, so stage 5 changes how these modules are
// built and fetched, not where they are or what they declare.
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

// Sorted by the `order` each app declares, then by id. Discovery order is
// filesystem order, and the switcher's siblings read in a deliberate order that
// alphabetising would silently change; `order` is how an app states its place
// without this file knowing what that place is.
export const APPS: AppModule[] = Object.values(
  [...declared(IN_TREE), ...declared(EXTRACTED)].reduce<Record<string, AppModule>>(
    (acc, a) => {
      acc[a.id] = a;
      return acc;
    },
    {},
  ),
).sort(
  (x, y) =>
    (x.order ?? Number.MAX_SAFE_INTEGER) - (y.order ?? Number.MAX_SAFE_INTEGER) ||
    x.id.localeCompare(y.id),
);

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
