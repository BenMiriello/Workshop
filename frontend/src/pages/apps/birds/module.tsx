// This app's frontend declaration: its routes, breadcrumbs, icon and card status.
//
// Moved out of `src/apps/registry.tsx` on 2026-09-02, where a literal APPS
// array named every app, so the shell hardcoded which apps exist. The registry
// discovers these modules now and names none. See `src/apps/appModule.ts`.

import { Route } from "react-router-dom";
import type { AppModule, AppStatus } from "../../../apps/appModule";
import { makeChatDock } from "../../../apps/appShell";
import { lazyPage } from "../../../lib/lazyPage";
import { queryClient } from "../../../lib/queryClient";

const Birds = lazyPage(() => import("../Birds"));
const Locations = lazyPage(() => import("./Locations"));
const LocationDetail = lazyPage(() => import("./LocationDetail"));
const Devices = lazyPage(() => import("./Devices"));
const BirdDetail = lazyPage(() => import("./BirdDetail"));
const BirdsReview = lazyPage(() => import("./BirdsReview"));
const BirdSettings = lazyPage(() => import("./BirdSettings"));
const Recordings = lazyPage(() => import("./RecordingsPage"));
const Atlas = lazyPage(() => import("./Atlas"));
const ForecastWeek = lazyPage(() => import("./ForecastWeek"));
// This app runs inside its own responsive shell (sidebar / mobile tabs) with the
// persistent Location·Device·Time scope. Lazy like every other app page: eager,
// it put this app's stylesheet in the entry bundle for every visitor.
const BirdsLayout = lazyPage(() => import("./BirdsLayout"));

// EAGER, and the only one. A Provider is mounted around the whole session, so
// `AppProviders` reduces over it synchronously and cannot await a chunk without
// unmounting the app underneath it. `recorder.tsx` imports no CSS, so the cost
// is bounded, but a provider that must outlive navigation is exactly what the
// manifest should declare and the app loader should mount with the app's own
// bundle. Tracked as stage 5 of
// docs/platform-redesign/app-separation-and-github-2026-08-20.md.
import { RecorderProvider } from "./recorder";

async function status(): Promise<AppStatus> {
  try {
    const q = await import("./queries");
    const s = await queryClient.fetchQuery({
      queryKey: q.birdsKeys.summary(q.YARD_SUMMARY_PARAMS),
      queryFn: q.fetchYardSummary,
    });
    // `available` is unconditionally true from this route, so it answers
    // nothing and the COUNT is what says whether there is a feed here yet.
    // Reading the flag meant a fresh instance rendered "0 species · 0 today"
    // in the ok tone and the "no detections yet" branch was unreachable.
    if (!s.species_count) return { text: "no detections yet", tone: "muted" };
    return {
      text: `${s.species_count} species · ${s.today?.detections || 0} today`,
      tone: "ok",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

const id = "birds";
const home = `/apps/${id}`;

export const appModule: AppModule = {
  id,
  order: 70,
  icon: "bird",
  status,
  Provider: RecorderProvider,
  crumbs: {
    [home]: { label: "Birds", parent: "/apps" },
    [`${home}/locations`]: { label: "Locations", parent: home },
    [`${home}/devices`]: { label: "Devices", parent: home },
    [`${home}/settings`]: { label: "Settings", parent: home },
    [`${home}/recordings`]: { label: "Recordings", parent: home },
    [`${home}/atlas`]: { label: "Atlas", parent: home },
    [`${home}/location`]: { label: "Location", parent: `${home}/locations` },
    [`${home}/bird`]: { label: "Bird", parent: home },
  },
  routes: () => (
    <Route key={id} element={<BirdsLayout />}>
      <Route path={home} element={<Birds renderChatDock={makeChatDock(id)} />} />
      <Route path={`${home}/birds`} element={<BirdsReview />} />
      <Route path={`${home}/locations`} element={<Locations />} />
      <Route path={`${home}/location`} element={<LocationDetail />} />
      <Route path={`${home}/devices`} element={<Devices />} />
      <Route path={`${home}/bird`} element={<BirdDetail />} />
      <Route path={`${home}/settings`} element={<BirdSettings />} />
      <Route path={`${home}/recordings`} element={<Recordings />} />
      <Route path={`${home}/atlas`} element={<Atlas />} />
      <Route path={`${home}/forecast`} element={<ForecastWeek />} />
    </Route>
  ),
};
