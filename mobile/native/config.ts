// Runtime endpoints, from app.json `extra`. Two distinct URLs:
//   webUrl  — where the WebView loads the Workshop SPA
//   apiBase — where native modules hit the backend API (background location, uploads)
//
// In dev these differ (Vite :5173 vs uvicorn :8000); in prod (Tailscale Serve)
// they collapse to one origin with the backend under /api.

import Constants from "expo-constants";

type Extra = { webUrl?: string; apiBase?: string; tier?: "free" | "paid" };

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

/**
 * Entitlement tier. "free" until the operator enrols in the Apple Developer
 * Program ($99/yr); flipping app.json `extra.tier` to "paid" turns on the
 * push-dependent UI in the web app (it reads `WorkshopNative.tier`).
 */
export const TIER: "free" | "paid" = extra.tier ?? "free";

export const WEB_URL: string = extra.webUrl ?? extra.apiBase ?? "http://127.0.0.1:5173";

/** Backend API base WITHOUT a trailing slash. Native code appends paths like `/location/update`. */
export const API_BASE: string = (extra.apiBase ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}
