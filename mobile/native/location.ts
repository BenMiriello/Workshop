// Background location capability. Replaces OwnTracks: a registered TaskManager
// task posts every fix straight to the backend `/location/update`, even while
// the app is suspended (iOS keeps a location-mode app alive). FREE — no paid
// entitlement; `UIBackgroundModes:["location"]` is an Info.plist key.

import * as Location from "expo-location";
import * as TaskManager from "expo-task-manager";

import type {
  BackgroundTrackingOptions,
  LocationFix,
  PermissionState,
  TrackingStatus,
} from "@shared/native-bridge";
import { apiUrl } from "./config";
import { authHeaders } from "./auth";
import * as queue from "./locationQueue";

export const LOCATION_TASK = "workshop-bg-location";

let lastFix: LocationFix | undefined;
let currentMode: BackgroundTrackingOptions["mode"];

function toFix(loc: Location.LocationObject): LocationFix {
  const c = loc.coords;
  return {
    lat: c.latitude,
    lng: c.longitude,
    accuracy_m: c.accuracy ?? undefined,
    altitude_m: c.altitude ?? undefined,
    heading_deg: c.heading ?? undefined,
    speed_mps: c.speed ?? undefined,
    client_ts: new Date(loc.timestamp).toISOString(),
  };
}

// Returns whether the fix actually reached the backend. A network error or any
// non-2xx is a failure, so the caller can persist the fix to the offline queue
// instead of silently losing it.
async function postFix(fix: LocationFix): Promise<boolean> {
  try {
    const res = await fetch(apiUrl("/location/update"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ ...fix, source: "workshop-ios" }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Registered at module import (imported by app/_layout.tsx so it exists before
// iOS ever relaunches the app into the background to deliver a fix).
TaskManager.defineTask(LOCATION_TASK, async ({ data, error }) => {
  if (error) return;
  const locations = (data as { locations?: Location.LocationObject[] })?.locations ?? [];
  // Drain any backlog first so re-sent fixes stay in chronological order. If the
  // network is still down, `drained` is false and we append new fixes behind the
  // backlog rather than posting them out of order.
  const drained = await queue.flush(postFix);
  for (const loc of locations) {
    const fix = toFix(loc);
    lastFix = fix;
    if (drained) {
      const ok = await postFix(fix);
      if (!ok) await queue.enqueue(fix);
    } else {
      await queue.enqueue(fix);
    }
  }
});

function mapPermission(status: Location.PermissionStatus): PermissionState {
  switch (status) {
    case Location.PermissionStatus.GRANTED:
      return "granted";
    case Location.PermissionStatus.DENIED:
      return "denied";
    default:
      return "undetermined";
  }
}

export async function getCurrent(): Promise<LocationFix> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  if (status !== Location.PermissionStatus.GRANTED) {
    throw new Error("location permission denied");
  }
  const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
  const fix = toFix(loc);
  lastFix = fix;
  return fix;
}

export async function startBackgroundTracking(opts?: BackgroundTrackingOptions): Promise<void> {
  const fg = await Location.requestForegroundPermissionsAsync();
  if (fg.status !== Location.PermissionStatus.GRANTED) {
    throw new Error("location permission denied");
  }
  // "Always" is required to run while suspended; without it we still get
  // foreground tracking but not true background.
  await Location.requestBackgroundPermissionsAsync();

  const mode = opts?.mode ?? "significant";
  currentMode = mode;

  if (await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK)) {
    await Location.stopLocationUpdatesAsync(LOCATION_TASK);
  }

  await Location.startLocationUpdatesAsync(LOCATION_TASK, {
    accuracy: mode === "active" ? Location.Accuracy.High : Location.Accuracy.Balanced,
    distanceInterval: opts?.distanceM ?? (mode === "active" ? 25 : 120),
    // Low-power, OwnTracks-like behaviour when not in an active commute.
    deferredUpdatesInterval: mode === "active" ? 0 : 60_000,
    pausesUpdatesAutomatically: mode !== "active",
    activityType: Location.ActivityType.OtherNavigation,
    showsBackgroundLocationIndicator: mode === "active",
    foregroundService: undefined, // iOS-only target; Android would set this
  });
}

export async function stopBackgroundTracking(): Promise<void> {
  if (await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK)) {
    await Location.stopLocationUpdatesAsync(LOCATION_TASK);
  }
  currentMode = undefined;
}

export async function trackingStatus(): Promise<TrackingStatus> {
  const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK);
  const perm = await Location.getForegroundPermissionsAsync();
  return {
    tracking: started,
    mode: currentMode,
    last: lastFix,
    permission: mapPermission(perm.status),
    queued: await queue.depth(),
  };
}
