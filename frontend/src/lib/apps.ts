// The app entity (app-is-the-entity-2026-08-20.md), read side. One place that
// knows how to go from a URL's app slug (every /apps/<slug> route already
// carries one) to the surrogate id the nested /api/apps/{id}/... routes need
// -- a rename is a PATCH there, not a migration, so the id is never the slug
// (design doc sec 5.1).
//
// The list is fetched once per session and cached: every per-app tab needs
// this same lookup, and there is no reason for seven tabs on one app to each
// make their own /api/apps round trip.
import { useCallback, useEffect, useState } from "react";
import type { components } from "../api/schema.gen";

export type AppRecord = components["schemas"]["App"];

let listPromise: Promise<AppRecord[]> | null = null;

function fetchAppList(): Promise<AppRecord[]> {
  if (!listPromise) {
    listPromise = fetch("/api/apps")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.statusText}`))))
      .then((d: { apps?: AppRecord[] }) => d.apps ?? [])
      .catch((e) => {
        listPromise = null;
        throw e;
      });
  }
  return listPromise;
}

/** Force the next read to hit the network again (an app was just created,
 *  renamed or relinked elsewhere in this session). */
export function invalidateAppList(): void {
  listPromise = null;
}

/** Resolve one app by slug to its full record. `app` is `null` while loading
 *  or when the slug does not resolve to a known app id (installed-only apps
 *  with no /api/apps row yet -- see the module note in services/apps.py about
 *  the backfill); either way `err` explains why so a tab can show a real
 *  error state instead of an empty one. */
export function useApp(slug: string): { app: AppRecord | null; err: string | null; reload: () => void } {
  const [app, setApp] = useState<AppRecord | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    fetchAppList()
      .then((apps) => {
        const found = apps.find((a) => a.slug === slug) ?? null;
        setApp(found);
        if (!found) setErr(`no /api/apps record for slug ${slug}`);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  return { app, err, reload: load };
}

/** Every app slug that owns a task queue right now (a linked project tree).
 *  The one place `chatScope.ts`'s `taskScopeFor` needs this outside a route
 *  that already fetches its own app record -- see that module for why it
 *  cannot make this call itself (it is a pure function, read at render time). */
export function linkedAppSlugs(): Promise<Set<string>> {
  return fetchAppList().then((apps) => new Set(apps.filter((a) => a.project_slug).map((a) => a.slug)));
}
