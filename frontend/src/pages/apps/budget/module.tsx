// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import type { AppModule } from "../../../apps/appModule";
import { AppGroupNav, makeChatDock } from "../../../apps/appShell";
import { lazyPage } from "../../../lib/lazyPage";

const Budget = lazyPage(() => import("../Budget"));

const id = "budget";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 30,
  icon: "piggy-bank",
  status: async () => ({ text: "plan & simulate", tone: "muted" }),
  group: "money",
  link: { label: "Budget", to: home },
  crumbs: { [home]: { label: "Budget", parent: "/apps" } },
  routes: () => (
    <Route
      key={id}
      path={home}
      element={<Budget groupNav={<AppGroupNav />} renderChatDock={makeChatDock(id)} />}
    />
  ),
};
