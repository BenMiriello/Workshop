// The app declaration contract for the frontend shell.
//
// THIS FILE NAMES NO APP. It is the counterpart to backend/services/app_spec.py
// and exists for the same reason: `apps/registry.tsx` used to hold both the
// contract AND a literal array of every app, so the file that defines what an
// app IS also decided which apps EXIST, and adding one meant editing platform
// source.
//
// An app now declares itself in its own `pages/apps/<app>/module.tsx`, exporting
// `appModule: AppModule`. The registry discovers those and never names one.

import type { ComponentType, ReactNode } from "react";

export type CrumbNode = { label: string; parent?: string };

/** One line of live state for the launcher card. */
export type AppStatus = { text: string; tone: "muted" | "ok" | "err" };

export type AppModule = {
  id: string;
  /** Breadcrumb nodes for this app's routes, keyed by exact path. */
  crumbs: Record<string, CrumbNode>;
  /** Apps sharing a group id render as each other's switcher siblings. */
  group?: string;
  /** Label + landing route for the switcher. Omit to stay out of it. */
  link?: { label: string; to: string };
  /** Where this app sorts among the others. Declared by the app because
   *  discovery order is filesystem order, and the switcher's siblings read in a
   *  deliberate order that alphabetising would silently change. Absent sorts
   *  last, then by id, so the list is stable either way. */
  order?: number;
  /** Old top-level paths this app absorbed, as `{ from: to }`. The shell
   *  renders each as a <Navigate replace>.
   *
   *  DECLARED BY THE APP because the path is the app's history, not the
   *  platform's. Two of these were hardcoded in `App.tsx` until 2026-09-04 --
   *  a platform route table naming two specific apps, which is exactly what
   *  `check_no_app_names.py` counts. An app that never had a legacy path
   *  declares nothing and the shell renders nothing. */
  redirects?: Record<string, string>;
  /** A provider the shell mounts for the whole session while installed. */
  Provider?: ComponentType<{ children: ReactNode }>;
  /** The app's icon, DECLARED not imported: an icon token (see lib/appIcon),
   *  a url/path for a custom image, or absent for a monogram of its name.
   *  It was `Icon?: ComponentType` holding a lucide component, which put "this
   *  particular app wants a bird" in platform source. */
  icon?: string;
  /** Live state for the launcher card. The launcher used to switch on app ids
   *  and fetch each app's endpoint itself; an app knows its own summary. */
  status?: () => Promise<AppStatus>;
  /** A panel this app contributes to the Settings screen, if any. */
  Settings?: ComponentType;
  /** Legacy `?tab=` values on a retired platform page that now belong to this
   *  app, mapped to where they should land. Declared by the app because WHICH
   *  app absorbed a retired surface is a fact about that app; the platform page
   *  merges them and knows none of the names. */
  legacyTabs?: Record<string, string>;
  /** The <Route> elements this app contributes under /apps. */
  routes: () => ReactNode;
};
