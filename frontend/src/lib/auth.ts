// Auth client for the Workshop SPA (browser + Expo WebView).
//
// Backend: backend/routes/auth.py. Authentication is an opaque, server-side
// session in an HttpOnly cookie — JS can never read it (XSS-safe) and the server
// can revoke it. There is no token in localStorage and no client-side refresh.
//
// Responsibilities here:
//   * patch window.fetch so every /api request sends the session cookie
//     (credentials: 'include') and the CSRF header (X-Workshop-Client) the
//     backend requires on cookie-authenticated mutations,
//   * expose login / register / logout / changePassword / fetchMe.

export interface AuthUser {
  id: number;
  email: string;
  name?: string | null;
  is_operator: boolean;
}

import { native } from "../native/bridge";

export class AuthError extends Error {}

const CSRF_HEADER = "X-Workshop-Client";

// Captured before we patch window.fetch so the auth calls bypass the patch.
let rawFetch: typeof fetch =
  typeof window !== "undefined" ? window.fetch.bind(window) : fetch;

// ---------------------------------------------------------------- fetch patch

function isApiUrl(url: string): boolean {
  if (url.startsWith("/api")) return true;
  try {
    return new URL(url, window.location.origin).pathname.startsWith("/api");
  } catch {
    return false;
  }
}

let patched = false;

/** Patch window.fetch to carry the session cookie + CSRF header on /api calls. */
export function installAuthFetch(): void {
  if (patched || typeof window === "undefined") return;
  patched = true;
  const orig = window.fetch.bind(window);
  rawFetch = orig;

  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;

    if (!isApiUrl(url)) return orig(input, init);

    const headers = new Headers(
      init.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    // The backend requires this header on cookie-authenticated mutations; it is
    // harmless on reads. A cross-site form cannot set a custom header, so this
    // (with SameSite=Strict) defeats CSRF.
    if (!headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, "1");

    return orig(input, { ...init, headers, credentials: "include" });
  };
}

// ---------------------------------------------------------------- api

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    const d = data?.detail;
    if (typeof d === "string") return d;
    // ADR 0023 (docs/decisions/0023-the-http-contract.md): the backend's
    // registered handler upgrades a string `detail` into
    // `{code, message, hint}` from one place, covering every raise site.
    if (d && typeof d === "object" && typeof d.message === "string" && d.message) return d.message;
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

const JSON_HEADERS = { "Content-Type": "application/json", [CSRF_HEADER]: "1" };

/**
 * Inside the Expo shell, mint a revocable device token for the native background
 * producers (location/audio) — they run outside the WebView and can't use the
 * cookie. Hand it to native via the bridge's Keychain-backed secureStore. No-op
 * in a plain browser. Best-effort and fire-and-forget.
 */
async function provisionDeviceToken(): Promise<void> {
  const n = native();
  if (!n?.has?.("secureStore")) return;
  try {
    const r = await rawFetch("/api/auth/device-token", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ label: "ios" }),
    });
    if (!r.ok) return;
    const token = (await r.json())?.device_token;
    if (token) await n.secureStore.set("ws-device-token", token);
  } catch {
    /* native producers stay unauthenticated until the next login attempt */
  }
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const r = await rawFetch("/api/auth/login", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Sign in failed"));
  const user = (await r.json()).user as AuthUser;
  void provisionDeviceToken();
  return user;
}

export async function register(
  email: string,
  password: string,
  name?: string,
): Promise<AuthUser> {
  const r = await rawFetch("/api/auth/register", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({ email, password, name: name || undefined }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Could not create account"));
  const user = (await r.json()).user as AuthUser;
  void provisionDeviceToken();
  return user;
}

/** Resolve the current user from the session cookie, or null if not signed in. */
export async function fetchMe(): Promise<AuthUser | null> {
  try {
    const r = await rawFetch("/api/auth/me", { credentials: "include" });
    if (r.ok) return (await r.json()) as AuthUser;
  } catch {
    /* network error — treat as anonymous, the guard will show login */
  }
  return null;
}

export async function logout(everywhere = false): Promise<void> {
  try {
    await rawFetch("/api/auth/logout", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ all_devices: everywhere }),
    });
  } catch {
    /* best-effort server-side revoke */
  }
}

export async function changePassword(current: string, next: string): Promise<void> {
  const r = await rawFetch("/api/auth/password", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({ current_password: current, new_password: next }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Could not change password"));
}
