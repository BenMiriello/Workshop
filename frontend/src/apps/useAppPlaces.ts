import { useQuery } from "@tanstack/react-query";
import { appPlaces } from "./registry";
import { queryKeys } from "../lib/queryKeys";

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
// own screens are; an app without UI lands on its generic tabs, which the
// launcher already names in `route`.

export type AppPlace = { id: string; name: string; home: string; icon?: string };

type LauncherApp = { id: string; title?: string; icon?: string; route?: string; href?: string };

async function fetchLauncher(): Promise<LauncherApp[]> {
  const r = await fetch("/api/base-apps/launcher");
  if (!r.ok) throw new Error(`launcher ${r.status}`);
  const j = (await r.json()) as { apps?: LauncherApp[] };
  return j.apps ?? [];
}

/** Every app this installation has, in launcher order.
 *
 *  Falls back to the bundled registry while the read is in flight or if it
 *  fails, so the dock never empties: an app list that blinks out on a slow
 *  request is worse than one that is briefly short. */
export function useAppPlaces(): AppPlace[] {
  const bundled = appPlaces();
  const q = useQuery({
    queryKey: queryKeys.apps.launcher(),
    queryFn: fetchLauncher,
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (!q.data) return bundled;

  const byId = new Map(bundled.map((a) => [a.id, a]));
  const out: AppPlace[] = [];
  for (const app of q.data) {
    // An external app (href, no route) is a link out, not a place in the shell.
    if (!app.route && app.href) continue;
    const known = byId.get(app.id);
    out.push({
      id: app.id,
      name: known?.name ?? app.title ?? app.id,
      // AN APP'S HOME IS ITS BASE PATH, NOT THE LAUNCHER'S LANDING ROUTE.
      //
      // `places.ts` uses `home` for two things: the place's URL PREFIX, and the
      // stem it builds every tab URL from (`home + t.suffix`). The launcher's
      // `route` is a landing hint and for an app with no bundled UI it is a
      // deep link (`/apps/x/chats`), so using it here made the prefix miss
      // every other tab and built tab URLs like `/apps/x/chats/files`. The
      // effect: opening such an app showed the WORKSHOP sidebar instead of the
      // app's own, and none of its seven tabs was reachable.
      //
      // An app that ships UI keeps the registry's home, because that registry
      // knows where its own screens live.
      home: known?.home ?? `/apps/${app.id}`,
      icon: known?.icon ?? app.icon,
    });
  }
  return out.length > 0 ? out : bundled;
}
