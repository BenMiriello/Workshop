// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import type { AppModule } from "../../../apps/appModule";
import { lazyPage } from "../../../lib/lazyPage";

const HealthSettings = lazyPage(() => import("./HealthSettings"));

// This app has no page of its own: a settings panel is its entire frontend.
// An app contributing no routes is a normal case, not a special one.
export const appModule: AppModule = {
  id: "health",
  order: 10,
  Settings: HealthSettings,
  crumbs: {},
  routes: () => null,
};
