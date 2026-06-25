// Native-side credential for background producers (location, audio).
//
// These run in the RN process, OUTSIDE the WebView's cookie + patched fetch, so
// they cannot use the session cookie. Instead the WebView (which IS signed in via
// the cookie) mints a revocable DEVICE TOKEN once (POST /auth/device-token) and
// hands it down through the bridge's secureStore under the key below. The native
// producers read it here and send it as a Bearer; the backend resolves it to the
// same user (deps_user.current_user) and scopes their writes to that owner.
//
// No JWT, no client-side refresh: a device token is opaque and long-lived, and
// is revoked server-side ("sign out everywhere" or per-device in Settings).

import * as SecureStore from "expo-secure-store";

// Must match the web key after the bridge's "workshop." prefix
// (frontend writes it via window.WorkshopNative.secureStore.set).
const DEVICE_KEY = "workshop.ws-device-token";

/** Store the device token handed down by the WebView after login. */
export async function setDeviceToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(DEVICE_KEY, token);
}

/** Sign out on the native side: drop the device token. */
export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(DEVICE_KEY);
}

/** Authorization header for native API calls; empty when no device token yet. */
export async function authHeaders(): Promise<Record<string, string>> {
  const t = await SecureStore.getItemAsync(DEVICE_KEY);
  return t ? { Authorization: `Bearer ${t}` } : {};
}
