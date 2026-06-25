// Native-side access to the product-auth session.
//
// The web app (frontend/src/lib/auth.ts) owns login and persists its access +
// refresh tokens through the secureStore bridge, which prefixes Keychain keys
// with "workshop.". The native background producers (location, audio) run in the
// RN process — outside the WebView's patched fetch — so they read those same
// tokens here and attach the Bearer themselves. On the tailnet the backend
// trusts the peer and ignores the header; off-tailnet this is what authenticates
// a background fix as the logged-in user.

import * as SecureStore from "expo-secure-store";

import { apiUrl } from "./config";

// Must match the web keys after the bridge's "workshop." prefix.
const ACCESS_KEY = "workshop.ws-auth-access";
const REFRESH_KEY = "workshop.ws-auth-refresh";

/** Manually set the access token (debug screen override). */
export async function setToken(access: string): Promise<void> {
  await SecureStore.setItemAsync(ACCESS_KEY, access);
}

/** Sign out on the native side: drop both tokens. */
export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(ACCESS_KEY);
  await SecureStore.deleteItemAsync(REFRESH_KEY);
}

function decodeExp(jwt: string): number | null {
  try {
    const payload = jwt.split(".")[1];
    if (!payload || typeof atob !== "function") return null;
    const exp = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

let refreshing: Promise<string | null> | null = null;

async function doRefresh(): Promise<string | null> {
  const rt = await SecureStore.getItemAsync(REFRESH_KEY);
  if (!rt) return null;
  try {
    const r = await fetch(apiUrl("/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    // Refresh rotates server-side, so persist BOTH new tokens or the next
    // refresh will present a revoked one.
    await SecureStore.setItemAsync(ACCESS_KEY, data.access_token);
    await SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token);
    return data.access_token as string;
  } catch {
    return null;
  }
}

/** Single-flight refresh so a burst of background posts triggers one round-trip. */
function refreshAccess(): Promise<string | null> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

async function validAccessToken(): Promise<string | null> {
  const t = await SecureStore.getItemAsync(ACCESS_KEY);
  if (t) {
    const exp = decodeExp(t);
    // Refresh a minute early so a fix isn't posted with a just-expired token.
    if (exp == null || exp * 1000 > Date.now() + 60_000) return t;
  }
  return refreshAccess();
}

/** Authorization header for native API calls; empty when not signed in. */
export async function authHeaders(): Promise<Record<string, string>> {
  const t = await validAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
