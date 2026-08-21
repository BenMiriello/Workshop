// Unified device-location helper. Prefers the native bridge (true background
// tracking, posts straight to the backend); falls back to the browser
// Geolocation API (foreground-only watch that POSTs to /location/update).
//
// Same call sites work in the mobile shell and in a desktop/Safari browser.

import type { LocationFix, BackgroundTrackingOptions } from "./types";
import { native } from "./bridge";

const API = "/api"; // vite proxy + tailnet serve both expose the backend here

/** One-shot current position from the best available source. */
export async function getCurrentFix(): Promise<LocationFix> {
  const n = native();
  if (n?.has("location")) return n.location.getCurrent();

  return new Promise<LocationFix>((resolve, reject) => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      reject(new Error("geolocation unavailable"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) =>
        resolve({
          lat: p.coords.latitude,
          lng: p.coords.longitude,
          accuracy_m: p.coords.accuracy ?? undefined,
          altitude_m: p.coords.altitude ?? undefined,
          heading_deg: p.coords.heading ?? undefined,
          speed_mps: p.coords.speed ?? undefined,
          client_ts: new Date().toISOString(),
        }),
      (e) => reject(new Error(e.message)),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 },
    );
  });
}

let webWatchId: number | null = null;

function postFix(fix: LocationFix, source: string): void {
  void fetch(`${API}/location/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...fix, source }),
  }).catch(() => {});
}

/**
 * Start reporting this device's location.
 * - In the shell: hands off to native background tracking (works while suspended).
 * - In a browser: foreground `watchPosition` that POSTs each fix (no true background).
 */
export async function startDeviceTracking(opts?: BackgroundTrackingOptions): Promise<void> {
  const n = native();
  if (n?.has("location")) {
    await n.location.startBackgroundTracking(opts);
    return;
  }
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    throw new Error("geolocation unavailable");
  }
  if (webWatchId != null) return;
  webWatchId = navigator.geolocation.watchPosition(
    (p) =>
      postFix(
        {
          lat: p.coords.latitude,
          lng: p.coords.longitude,
          accuracy_m: p.coords.accuracy ?? undefined,
          altitude_m: p.coords.altitude ?? undefined,
          heading_deg: p.coords.heading ?? undefined,
          speed_mps: p.coords.speed ?? undefined,
          client_ts: new Date().toISOString(),
        },
        "workshop-web",
      ),
    () => {},
    { enableHighAccuracy: opts?.mode !== "significant", maximumAge: 5000 },
  );
}

export async function stopDeviceTracking(): Promise<void> {
  const n = native();
  if (n?.has("location")) {
    await n.location.stopBackgroundTracking();
    return;
  }
  if (webWatchId != null && navigator.geolocation) {
    navigator.geolocation.clearWatch(webWatchId);
    webWatchId = null;
  }
}

/** True when continuous background tracking (suspended app) is actually possible. */
export function backgroundTrackingSupported(): boolean {
  return !!native()?.has("location");
}
