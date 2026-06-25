// Product-auth client for the Workshop SPA.
//
// Backend layer lives in backend/routes/auth.py (register/login/refresh/me).
// Responsibilities here:
//   * hold the access token in memory and persist the token pair (localStorage
//     in a browser; the native secureStore via the mobile bridge when present),
//   * patch window.fetch so every /api request carries the Bearer access token
//     and transparently refreshes once on a 401,
//   * expose login / register / logout / fetchMe for the AuthProvider.
//
// The same code runs in a plain browser and inside the Expo WebView shell.

import { native } from "../native/bridge";

export interface AuthUser {
  id: number;
  email: string;
  name?: string | null;
  is_operator: boolean;
}

interface Session {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

export class AuthError extends Error {}

const ACCESS_KEY = "ws-auth-access";
const REFRESH_KEY = "ws-auth-refresh";

let accessToken: string | null = null;
let refreshToken: string | null = null;

// Captured before we patch window.fetch, so the auth endpoints (login/refresh)
// bypass the patch and can never recurse through it.
let rawFetch: typeof fetch =
  typeof window !== "undefined" ? window.fetch.bind(window) : fetch;

// ---------------------------------------------------------------- storage

function nativeSecureStore() {
  const n = native();
  return n?.has("secureStore") ? n.secureStore : undefined;
}

async function storageGet(key: string): Promise<string | null> {
  const ss = nativeSecureStore();
  if (ss) {
    try {
      return await ss.get(key);
    } catch {
      return null;
    }
  }
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

async function storageSet(key: string, value: string): Promise<void> {
  const ss = nativeSecureStore();
  if (ss) {
    try {
      await ss.set(key, value);
      return;
    } catch {
      /* fall through to localStorage */
    }
  }
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode / quota — token simply won't persist across reloads */
  }
}

async function storageRemove(key: string): Promise<void> {
  const ss = nativeSecureStore();
  if (ss) {
    try {
      await ss.remove(key);
    } catch {
      /* ignore */
    }
  }
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------- session

export function getAccessToken(): string | null {
  return accessToken;
}

export function hasStoredSession(): boolean {
  return !!(accessToken || refreshToken);
}

/** Load any persisted tokens into memory. Call once at boot before fetchMe. */
export async function hydrate(): Promise<void> {
  accessToken = await storageGet(ACCESS_KEY);
  refreshToken = await storageGet(REFRESH_KEY);
}

async function persist(session: Session): Promise<void> {
  accessToken = session.access_token;
  refreshToken = session.refresh_token;
  await storageSet(ACCESS_KEY, session.access_token);
  await storageSet(REFRESH_KEY, session.refresh_token);
}

export async function clearSession(): Promise<void> {
  accessToken = null;
  refreshToken = null;
  await storageRemove(ACCESS_KEY);
  await storageRemove(REFRESH_KEY);
}

// ---------------------------------------------------------------- refresh

let refreshing: Promise<boolean> | null = null;

function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return Promise.resolve(false);
  // Single-flight: a burst of 401s triggers exactly one refresh round-trip.
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const r = await rawFetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!r.ok) {
          await clearSession();
          return false;
        }
        await persist(await r.json());
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

// ---------------------------------------------------------------- fetch patch

function isApiUrl(url: string): boolean {
  // Relative /api/* (the SPA's only backend path) or an absolute same-origin one.
  if (url.startsWith("/api")) return true;
  try {
    return new URL(url, window.location.origin).pathname.startsWith("/api");
  } catch {
    return false;
  }
}

let patched = false;

/** Patch window.fetch to attach the Bearer token and auto-refresh on 401. */
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
    if (accessToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }

    let res = await orig(input, { ...init, headers });

    // One transparent refresh + retry on expiry. Never retry the refresh call.
    if (res.status === 401 && refreshToken && !url.includes("/auth/refresh")) {
      const ok = await tryRefresh();
      if (ok && accessToken) {
        headers.set("Authorization", `Bearer ${accessToken}`);
        res = await orig(input, { ...init, headers });
      }
    }
    return res;
  };
}

// ---------------------------------------------------------------- api

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const r = await rawFetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Sign in failed"));
  const session: Session = await r.json();
  await persist(session);
  return session.user;
}

export async function register(
  email: string,
  password: string,
  name?: string,
): Promise<AuthUser> {
  const r = await rawFetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, name: name || undefined }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Could not create account"));
  const session: Session = await r.json();
  await persist(session);
  return session.user;
}

/** Resolve the current user from a stored session, or null if not signed in. */
export async function fetchMe(): Promise<AuthUser | null> {
  if (!hasStoredSession()) return null;
  try {
    const r = await fetch("/api/auth/me"); // patched: attaches token, refreshes once
    if (r.ok) return (await r.json()) as AuthUser;
  } catch {
    /* network error — treat as anonymous, the guard will show login */
  }
  return null;
}

export async function logout(everywhere = false): Promise<void> {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        everywhere ? { all_devices: true } : { refresh_token: refreshToken },
      ),
    });
  } catch {
    /* best-effort server-side revoke; we clear locally regardless */
  }
  await clearSession();
}

export async function changePassword(current: string, next: string): Promise<void> {
  const r = await fetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: current, new_password: next }),
  });
  if (!r.ok) throw new AuthError(await errorMessage(r, "Could not change password"));
  // The server rotates the session on a password change; persist the new pair.
  await persist(await r.json());
}
