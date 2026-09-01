import { useQuery } from "@tanstack/react-query";
import { appPlaces } from "./registry";
import { queryKeys } from "../lib/queryKeys";
import type { components } from "../api/schema.gen";

// WHICH APPS EXIST is a question for the platform, not for the bundle.
//
// The dock and the dashboard card both built their tile list from `appPlaces()`,
// which walks the frontend registry: the apps that ship UI code in this bundle.
// That is the right list for "which app owns this route" and the wrong one for
// "which apps does this installation have". An app the platform knew about any
// other way had no tile, no card and no link anywhere, while its chats, memory
// and tickets were all being served.
//
// The launcher is the platform's answer. The registry still supplies the home
// path and icon for an app that ships UI, because it knows where that app's
// own screens are.
//
// AND WHAT EACH APP OPENS ON is the platform's answer too, as of 2026-09-01:
// the launcher card carries the app's resolved `surface`
// (backend/services/app_surface.py) -- where it opens and which per-app tabs it
// has. Before that, every app opened on `/apps/<slug>`, which redirected to
// `/apps/<slug>/chats`, and carried all seven tabs whether or not any of them
// could hold anything. See ticket 7d8d733a.

type LauncherCard = components["schemas"]["LauncherCard"];
type AppSurface = components["schemas"]["AppSurface"];

export type AppPlace = {
  id: string;
  name: string;
  /** The app's BASE path: its route prefix and the stem of every tab URL. */
  home: string;
  icon?: string;
  /** Where the app opens. Absent only while the platform's answer is loading. */
  landing?: string;
  /** The tabs the app declared, in its order, or absent for "all it can have". */
  tabs?: readonly string[];
  projectSlug?: string | null;
  hasFiles?: boolean;
  /** Whether this app ships its own screens at `home`. */
  ownScreen?: boolean;
};

async function fetchLauncher(): Promise<LauncherCard[]> {
  const r = await fetch("/api/base-apps/launcher");
  if (!r.ok) throw new Error(`launcher ${r.status}`);
  // LauncherCards, named rather than re-described. This list decides which
  // apps exist in the shell at all, so an envelope rename here would empty the
  // whole app switcher and read as "no apps installed".
  const j = (await r.json()) as components["schemas"]["LauncherCards"];
  return j.apps ?? [];
}

function place(app: LauncherCard, known: AppPlace | undefined): AppPlace {
  // AN APP'S HOME IS ITS BASE PATH, NOT THE LAUNCHER'S LANDING ROUTE.
  //
  // `places.ts` uses `home` for two things: the place's URL PREFIX, and the
  // stem it builds every tab URL from (`home + t.suffix`). The launcher's
  // `route` is where the app OPENS, and for an app with no bundled UI that is
  // a deep link into one of its tabs, so using it here made the prefix miss
  // every other tab and built tab URLs like `/apps/x/tickets/files`. The
  // effect: opening such an app showed the WORKSHOP sidebar instead of the
  // app's own, and none of its tabs was reachable.
  //
  // An app that ships UI keeps the registry's home, because that registry
  // knows where its own screens live.
  const surface = (app.surface ?? null) as AppSurface | null;
  return {
    id: app.id,
    name: known?.name ?? app.title ?? app.id,
    home: known?.home ?? `/apps/${app.id}`,
    icon: known?.icon ?? app.icon ?? undefined,
    landing: surface?.home ?? app.route ?? undefined,
    tabs: surface?.tabs ?? undefined,
    projectSlug: surface?.project_slug ?? null,
    hasFiles: surface?.files ?? false,
    ownScreen: !!known,
  };
}

/** Every app this installation has, in launcher order, and whether the
 *  platform has answered yet.
 *
 *  While the read is in flight (or if it fails) this falls back to the bundled
 *  registry, so the dock never empties: an app list that blinks out on a slow
 *  request is worse than one that is briefly short. `ready` is for the one
 *  caller that must not act on the fallback -- the redirect off an app's bare
 *  path, which would otherwise send the operator somewhere the app did not
 *  choose and then be unable to take it back. */
export function useAppPlacesState(): { apps: AppPlace[]; ready: boolean } {
  const bundled = appPlaces();
  const q = useQuery({
    queryKey: queryKeys.apps.launcher(),
    queryFn: fetchLauncher,
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (!q.data) return { apps: bundled, ready: q.isError };

  const byId = new Map(bundled.map((a) => [a.id, a]));
  const out: AppPlace[] = [];
  for (const app of q.data) {
    // An external app (href, no route) is a link out, not a place in the shell.
    if (!app.route && app.href) continue;
    out.push(place(app, byId.get(app.id)));
  }
  return out.length > 0 ? { apps: out, ready: true } : { apps: bundled, ready: true };
}

/** Every app this installation has, in launcher order. */
export function useAppPlaces(): AppPlace[] {
  return useAppPlacesState().apps;
}
