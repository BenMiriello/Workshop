// App auth uses the EXISTING backend model (deps_auth): trusted on the tailnet
// (loopback + CGNAT), otherwise a Bearer service token. The operator's phone is
// on the tailnet via the Tailscale app, so this is normally a no-op; the stored
// token is the fallback for any off-tailnet origin. This deliberately is NOT a
// per-user login system — that would duplicate deps_auth and is the multi-user
// cruft the single-operator setup avoids. Generalised shape (a token), one user.

import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "workshop.apiToken";

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function setToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

/** Authorization header for native API calls; empty when no token is stored. */
export async function authHeaders(): Promise<Record<string, string>> {
  const t = await getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
