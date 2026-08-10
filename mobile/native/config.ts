// Runtime endpoints — a SINGLE backend origin, resolved at build time.
//
// The mobile app is operator-only (it is NOT meant for public replication, unlike
// the Workshop web UI), so it just points at one Workshop backend. `/api` is
// proxied to the backend at every origin — the public edge in prod, the vite dev
// server in dev — so the same origin serves both the WebView (loads the SPA) and
// the native producers (which POST under `/api/...`). No host is committed to the
// repo; override for dev via an env var or app.json.
//
// Precedence:
//   1. EXPO_PUBLIC_WORKSHOP_URL   (env; put in mobile/.env.local for dev, e.g. a
//      tailnet URL — gitignored, so nothing private lands in git)
//   2. app.json  extra.serverUrl  (if you'd rather set it there)
//
// There is NO default. A build that was never told which backend it talks to
// would otherwise ship pointing at whoever built it.

import Constants from "expo-constants";

type Extra = { serverUrl?: string; webUrl?: string; tier?: "free" | "paid" };

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

const DEFAULT_SERVER = "";

const SERVER_URL: string = (
  process.env.EXPO_PUBLIC_WORKSHOP_URL ||
  extra.serverUrl ||
  extra.webUrl ||              // back-compat with older single-URL configs
  DEFAULT_SERVER
).replace(/\/+$/, "");

if (!SERVER_URL) {
  throw new Error(
    "No Workshop backend configured. Set EXPO_PUBLIC_WORKSHOP_URL in " +
      "mobile/.env.local (or extra.serverUrl in app.json) before building.",
  );
}

/**
 * Entitlement tier. "free" until the operator enrols in the Apple Developer
 * Program ($99/yr); flipping app.json `extra.tier` to "paid" turns on the
 * push-dependent UI in the web app (it reads `WorkshopNative.tier`).
 */
export const TIER: "free" | "paid" = extra.tier ?? "free";

/** Where the WebView loads the Workshop SPA. */
export const WEB_URL: string = SERVER_URL;

/**
 * Backend API base WITHOUT a trailing slash. Native code appends paths like
 * `/location/update`; they resolve to `<origin>/api/location/update`, which every
 * origin proxies to the backend (which also strips the `/api` prefix).
 */
export const API_BASE: string = `${SERVER_URL}/api`;

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}
