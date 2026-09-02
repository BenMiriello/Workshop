// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import type { AppModule } from "../../../apps/appModule";
import { AppGroupNav, makeChatDock } from "../../../apps/appShell";
import { lazyPage } from "../../../lib/lazyPage";

const Commute = lazyPage(() => import("../Commute"));

const id = "commute";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 50,
  icon: "train-front",
  status: async () => ({ text: "schedule & live status", tone: "muted" }),
  group: "place",
  link: { label: "Commute", to: home },
  crumbs: { [home]: { label: "Commute", parent: "/apps" } },
  routes: () => (
    <Route
      key={id}
      path={home}
      element={<Commute groupNav={<AppGroupNav />} renderChatDock={makeChatDock(id)} />}
    />
  ),
};
