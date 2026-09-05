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
      // AppCollection, named rather than re-described, and named AT the decode:
      // this one list backs every per-app tab, so an envelope rename here empties
      // all seven at once.
      .then((r) => (r.ok
        ? (r.json() as Promise<components["schemas"]["AppCollection"]>)
        : Promise.reject(new Error(`${r.status} ${r.statusText}`))))
      .then((d) => d.apps ?? [])
      .catch((e) => {
        listPromise = null;
        throw e;
      });
  }
  return listPromise;
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

// ---- the app's own addresses ------------------------------------------------
// The three top-level lists (/runs, /changes, /deploys) were deleted on
// 2026-09-01 (threads-branches-and-pages-2026-08-29.md section 6, "Retired,
// each in the commit that replaces it"). What replaced each of them is a view
// of ONE app, so every surface that used to send a reader to a global list now
// has to spell an app-scoped route instead. Spelled here once, for the reason
// `ticketHref` exists: a route written out by hand in nine places is a route
// that will be written wrong in one of them.

/** The lens the app's Work view opens on. `tickets` is the default and the
 *  addendum's primary axis, so it is spelled by LEAVING THE PARAM OFF -- the
 *  same rule `AppWorkTab` writes it back with, which is what keeps a plain
 *  `/apps/<slug>/work` the canonical address of the default view rather than
 *  one of two spellings of it. */
export type WorkLens = "tickets" | "branches" | "runs";

/** The app's Work view, optionally on one of its lenses. */
export function appWorkHref(slug: string, lens: WorkLens = "tickets"): string {
  const base = `/apps/${encodeURIComponent(slug)}/work`;
  return lens === "tickets" ? base : `${base}?lens=${lens}`;
}

/** The app's Overview, which absorbed the autonomy brake, the services, the
 *  deploys and the environment (section 7 step 4). */
export function appOverviewHref(slug: string): string {
  return `/apps/${encodeURIComponent(slug)}/overview`;
}
