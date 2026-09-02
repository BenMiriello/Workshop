// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import { timeAgoSeconds } from "@workshop/ui";
import type { AppModule, AppStatus } from "../../../apps/appModule";
import { AppGroupNav, makeChatDock } from "../../../apps/appShell";
import { lazyPage } from "../../../lib/lazyPage";
import { queryClient } from "../../../lib/queryClient";

const LocationApp = lazyPage(() => import("../Location"));

// THROUGH THIS APP'S OWN FETCHER, ON ITS OWN KEY. The launcher used to spell the
// URL and parse the body itself, which is how this card came to read `j.latest`
// from a route that answers with the record BARE: undefined for every possible
// response, so the card said "no pings yet" permanently while the app page one
// tap away showed a live position. Going through the app's exported fetcher
// makes the two readers unable to disagree, because there is only one of them.
// And through `fetchQuery`, so the probe reads the shared cache entry rather
// than issuing a second request for a fact the app already holds.
async function status(): Promise<AppStatus> {
  try {
    const loc = await import("./queries");
    // `null` is the real "nothing recorded yet"; a failed read THROWS and lands
    // in the catch below. The two used to be the same blank.
    const rec = await queryClient.fetchQuery({
      queryKey: loc.locationKeys.latest(),
      queryFn: loc.fetchLatest,
    });
    if (!rec?.ts) return { text: "no pings yet", tone: "muted" };
    const age = Math.max(0, Math.round((Date.now() - Date.parse(rec.ts)) / 1000));
    // A ping age is information, not health: significant-change tracking only
    // pings on movement, so hours-old while stationary is normal. Green only
    // when genuinely recent; otherwise neutral.
    return { text: `last ping ${timeAgoSeconds(age)}`, tone: age < 3600 ? "ok" : "muted" };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

const id = "location";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 40,
  icon: "map-pin",
  status,
  group: "place",
  link: { label: "Location", to: home },
  crumbs: { [home]: { label: "Location", parent: "/apps" } },
  routes: () => (
    <Route
      key={id}
      path={home}
      element={<LocationApp groupNav={<AppGroupNav />} renderChatDock={makeChatDock(id)} />}
    />
  ),
};
