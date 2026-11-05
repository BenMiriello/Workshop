// The apps this INSTALLATION has, asked of the platform instead of compiled in.
//
// `registry.tsx` discovers an app by scanning for `module.tsx` with
// `import.meta.glob`, which Vite resolves AT BUILD TIME. That is the whole
// reason installing an app has needed a shell rebuild: the catalog is a
// compile-time fact. This module is the runtime half. It reads
// `GET /base-apps/contributions`, turns each declaration into the same
// `AppModule` the rest of the shell already consumes, and publishes the result
// to a tiny store the registry merges.
//
// TOLERANT BY CONSTRUCTION, the same way `manifests._normalize_entry` is on the
// server: an unreachable endpoint, a malformed payload or one bad page entry
// yields fewer apps and never throws into render. The shell renders complete,
// with the apps it could read.
import { useEffect, useSyncExternalStore } from "react";
import { Route } from "react-router-dom";
import type { ComponentType, ReactNode } from "react";
import { HttpError, isTransientNetworkError } from "@workshop/ui";
import type { components } from "../api/schema.gen";
import { cappedBackoff, pauseForRetry } from "../lib/retryPause";
import { AppRouteHost } from "./AppRouteHost";
import type { AppDeclaration } from "./contract";
import { forgetLoadedApps } from "./loader";
import { setDeclaredApps } from "./registry";
import type { AppModule, AppStatus, CrumbNode } from "./appModule";

const CONTRIBUTIONS = "/api/base-apps/contributions";

/** How long the shell waits for an app's session-scoped provider before
 *  rendering without it. The shell is never held hostage to an app. */
const PROVIDER_BUDGET_MS = 3000;

export type DeclarationState = {
  /** The apps that declared a UI, as the shell's own catalog shape. */
  apps: AppModule[];
  /** False until the first read has finished, successfully or not. The routed
   *  area waits on this: mounting routes before the declarations are known
   *  would 404 a deep link into an app for one frame. */
  ready: boolean;
  /** Set when the read itself failed, so a surface can say so and offer Retry
   *  rather than showing an empty catalog as if nothing were installed. */
  error: string | null;
};

let state: DeclarationState = { apps: [], ready: false, error: null };
const listeners = new Set<() => void>();
let pending: Promise<void> | null = null;

function publish(next: DeclarationState): void {
  state = next;
  setDeclaredApps(next.apps);
  for (const fn of listeners) fn();
}

export function declarationState(): DeclarationState {
  return state;
}

export function subscribeDeclarations(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// --------------------------------------------------------------------------- //
// One declaration -> one AppModule
// --------------------------------------------------------------------------- //

function crumbs(app: AppDeclaration): Record<string, CrumbNode> {
  const out: Record<string, CrumbNode> = {};
  for (const page of app.pages ?? []) {
    if (page.crumb) {
      out[page.path] = { label: page.crumb.label, parent: page.crumb.parent ?? undefined };
    }
  }
  return out;
}

function routesFor(app: AppDeclaration): ReactNode {
  const pages = (app.pages ?? []).filter((p) => p.mount === "app.route");
  const bare = pages.filter((p) => !p.layout);
  const wrapped = new Map<string, typeof pages>();
  for (const page of pages) {
    if (!page.layout) continue;
    wrapped.set(page.layout, [...(wrapped.get(page.layout) ?? []), page]);
  }
  return (
    <>
      {bare.map((page) => (
        <Route
          key={page.path}
          path={page.path}
          element={<AppRouteHost app={app} exportName={page.export} />}
        />
      ))}
      {[...wrapped.entries()].map(([layout, group]) => (
        // The app declares which of its pages sit under which of its layouts;
        // the SHELL builds both routes. An app never constructs a <Route>.
        <Route key={`${app.id}:${layout}`} element={<AppRouteHost app={app} exportName={layout} />}>
          {group.map((page) => (
            <Route
              key={page.path}
              path={page.path}
              element={<AppRouteHost app={app} exportName={page.export} />}
            />
          ))}
        </Route>
      ))}
    </>
  );
}

function statusFor(app: AppDeclaration): (() => Promise<AppStatus>) | undefined {
  const endpoint = app.status_endpoint;
  if (!endpoint) return undefined;
  return async () => {
    try {
      const r = await fetch(endpoint.startsWith("/api") ? endpoint : `/api${endpoint}`);
      if (!r.ok) return { text: "unavailable", tone: "err" };
      const body = (await r.json()) as { text?: string; tone?: AppStatus["tone"] };
      return { text: String(body.text ?? ""), tone: body.tone ?? "muted" };
    } catch {
      return { text: "unavailable", tone: "err" };
    }
  };
}

function settingsFor(app: AppDeclaration): ComponentType | undefined {
  const panel = (app.settings ?? []).find((s) => s.mount === "app.settings");
  if (!panel) return undefined;
  const Panel = () => <AppRouteHost app={app} exportName={panel.export} />;
  Panel.displayName = `AppSettings(${app.id})`;
  return Panel;
}

/** Await an app's session-scoped provider entry, if it declared one.
 *
 *  THE ONE THING THAT CANNOT BE LAZY. `AppProviders` reduces over the provider
 *  list synchronously and cannot await a chunk without unmounting the app
 *  underneath it, so the provider is resolved BEFORE these declarations are
 *  published and therefore before the routed area first renders. On timeout or
 *  failure the app is published without it: its own routes will say so, and
 *  nothing else on the screen is affected. */
async function providerFor(app: AppDeclaration): Promise<ComponentType<{ children: ReactNode }> | undefined> {
  const url = app.urls?.provider;
  if (!url) return undefined;
  const budget = new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), PROVIDER_BUDGET_MS));
  const load = (async () => {
    try {
      const mod = (await import(/* @vite-ignore */ url.startsWith("/") ? `/api${url}` : url)) as {
        default?: { Provider?: ComponentType<{ children: ReactNode }> };
      };
      return mod.default?.Provider;
    } catch {
      return undefined;
    }
  })();
  return Promise.race([load, budget]);
}

async function toModule(app: AppDeclaration): Promise<AppModule> {
  return {
    id: app.id,
    order: app.order ?? undefined,
    icon: app.icon ?? undefined,
    group: app.group ?? undefined,
    link: app.link ? { label: app.link.label, to: app.link.to } : undefined,
    crumbs: crumbs(app),
    redirects: app.redirects ?? undefined,
    legacyTabs: app.legacy_tabs ?? undefined,
    status: statusFor(app),
    Settings: settingsFor(app),
    Provider: await providerFor(app),
    routes: () => routesFor(app),
  };
}

/** Whether this declaration can actually render its screens.
 *
 *  The registry prefers a HEALTHY runtime declaration over a build-time module
 *  of the same id, and prefers the build-time module over a broken one. That is
 *  what makes the migration of an app a sequence rather than a flag day: an app
 *  whose bundle is not built yet keeps rendering from the shell's own build,
 *  and flips to its own the moment that bundle exists. */
export function canActivate(app: AppDeclaration): boolean {
  if (app.error) return false;
  if (app.headless) return true;
  return Boolean(app.urls?.entry);
}

// --------------------------------------------------------------------------- //
// Reading them
// --------------------------------------------------------------------------- //

type Payload = components["schemas"]["AppContributions"];

/** What one read learned. `unanswered` is a restart window or a dropped
 *  connection, which says nothing about which apps exist; `signedOut` is the
 *  boot read racing the session check (main.tsx), to be asked again once there
 *  is a session. */
type ReadResult =
  | { kind: "answered"; state: DeclarationState }
  | { kind: "unanswered"; error: string }
  | { kind: "signedOut" };

async function read(): Promise<ReadResult> {
  let payload: Payload;
  try {
    const r = await fetch(CONTRIBUTIONS);
    if (r.status === 401) return { kind: "signedOut" };
    if (!r.ok) {
      const error = `${CONTRIBUTIONS} answered ${r.status}`;
      if (isTransientNetworkError(new HttpError(r.status, error))) return { kind: "unanswered", error };
      return { kind: "answered", state: { apps: [], ready: true, error } };
    }
    payload = (await r.json()) as Payload;
  } catch {
    return { kind: "unanswered", error: `${CONTRIBUTIONS} could not be reached` };
  }
  const declared = Array.isArray(payload?.apps) ? payload.apps : [];
  const usable = declared.filter((app) => app && app.id && canActivate(app));
  const apps = await Promise.all(usable.map(toModule));
  return { kind: "answered", state: { apps, ready: true, error: null } };
}

/** How long the FIRST read holds the routed area for a backend that is not
 *  answering, before the shell renders without the apps. Long enough to cover
 *  a restart; the read carries on in the background either way. */
const FIRST_READ_HOLD_MS = 10_000;

let generation = 0;

/** Read until the server answers. A failure to answer is never published as an
 *  empty catalog over one already on screen, and is never the session's final
 *  word: the read keeps asking (lib/retryPause) and publishes when it lands.
 *  Settles once there is something to render, so a caller is not held for the
 *  length of an outage. A newer load supersedes this one. */
function load(): Promise<void> {
  const gen = ++generation;
  const started = Date.now();
  return new Promise<void>((settle) => {
    void (async () => {
      for (let attempt = 0; gen === generation; attempt++) {
        const r = await read();
        if (gen !== generation) break;
        if (r.kind === "answered") {
          publish(r.state);
          break;
        }
        if (r.kind === "signedOut") {
          pending = null;
          break;
        }
        if (!state.ready && Date.now() - started >= FIRST_READ_HOLD_MS) {
          publish({ apps: [], ready: true, error: r.error });
        }
        if (state.ready) settle();
        await pauseForRetry(cappedBackoff(attempt));
      }
      settle();
    })();
  });
}

/** Read the declarations once per session. Idempotent and safe to call from
 *  every render. */
export function ensureDeclarations(): Promise<void> {
  if (pending) return pending;
  pending = load();
  return pending;
}

/** Read them again: after an app is installed or removed, and after a bundle
 *  failed to load with a hash this tab was holding. */
export function refreshDeclarations(): Promise<void> {
  forgetLoadedApps();
  pending = load();
  return pending;
}

/** Subscribe a component to the declarations, starting the read if it has not
 *  happened. The routed area reads `ready` from this. */
export function useAppDeclarations(): DeclarationState {
  const snapshot = useSyncExternalStore(subscribeDeclarations, declarationState, declarationState);
  useEffect(() => {
    void ensureDeclarations();
  }, []);
  return snapshot;
}
