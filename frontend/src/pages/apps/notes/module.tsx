// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import type { AppModule } from "../../../apps/appModule";
import { lazyPage } from "../../../lib/lazyPage";

const Notes = lazyPage(() => import("./Notes"));

// THE PATH IS THE SLUG, so it is DERIVED from the id rather than typed beside
// it. This app's crumb key and route were once spelled one letter short of its
// id. `appPlaces()` takes an app's `home` from the crumb key, so places.ts built
// every generic tab from the short path, and the generic route then resolved an
// app slug no record has: all seven tabs rendered a full-page "no such app"
// error. The reverse failed too -- on the path that DOES work, no place prefix
// matched, so the page column showed the Workshop root nav instead of this
// app's own.
//
// Two spellings of one name is the bug. There is now one, and the paths are
// built from it, so they cannot drift apart again.
const id = "notes-app";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 60,
  icon: "notebook-pen",
  crumbs: { [home]: { label: "Notes", parent: "/apps" } },
  // The retired Memory page's `?tab=vault`. It sent readers here, and the id
  // to build that path was exported from the shell's registry and imported by
  // that platform page -- a platform file naming this app. It is this app's
  // fact, so this app states it.
  legacyTabs: { vault: home },
  routes: () => <Route key={id} path={home} element={<Notes />} />,
};
