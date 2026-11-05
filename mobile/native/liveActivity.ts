// Live Activities (lock screen and Dynamic Island), one generic activity for
// every app; the contract is shared/native-live-activity.ts.
//
// The LOCAL-update version is FREE: ActivityKit lets the app start and update
// an activity whenever it is running (foreground, or background while a
// location session keeps it alive). The Swift side is the optional Expo module
// `workshop-live-activity`; a build without it reports isSupported() false and
// every other call rejects. Push-DRIVEN updates (the server updating an
// activity while the app is asleep) are the deferred paid feature.
//
// Every call takes the calling app's id first (the request's `app`, which the
// dispatcher passes). The owner is stored on the activity itself, so it holds
// across relaunches, and update and end refuse an activity another app started.

import { requireOptionalNativeModule } from "expo-modules-core";

import {
  ACTIVITY_STATUSES,
  APP_ID,
  type LiveActivityHandle,
  type LiveActivityState,
} from "@shared/native-bridge";

interface NativeLiveActivity {
  isSupported(): boolean;
  list(): { id: string; app: string }[];
  start(app: string, state: LiveActivityState): Promise<string>;
  update(id: string, state: LiveActivityState): Promise<void>;
  end(id: string): Promise<void>;
}

const Native = requireOptionalNativeModule<NativeLiveActivity>("WorkshopLiveActivity");

function need(): NativeLiveActivity {
  if (!Native) throw new Error("Live Activities are not in this build of the app");
  return Native;
}

function owner(app: string): string {
  if (!APP_ID.test(app)) throw new Error("A Live Activity belongs to an app: call through forApp");
  return app;
}

function owned(native: NativeLiveActivity, app: string, id: string): void {
  if (!native.list().some((a) => a.id === id && a.app === owner(app))) {
    throw new Error(`No Live Activity ${id} for this app`);
  }
}

function text(value: unknown, field: string, required: boolean): string | undefined {
  if (value === undefined || value === null || value === "") {
    if (required) throw new Error(`Live Activity ${field} is required`);
    return undefined;
  }
  if (typeof value !== "string") throw new Error(`Live Activity ${field} must be a string`);
  return value;
}

/** The state with only the contract's fields, checked; progress clamped. */
export function checked(state: LiveActivityState): LiveActivityState {
  const s = (state ?? {}) as unknown as Record<string, unknown>;
  if (!ACTIVITY_STATUSES.includes(s.status as LiveActivityState["status"])) {
    throw new Error(`Live Activity status must be one of ${ACTIVITY_STATUSES.join(", ")}`);
  }
  const out: LiveActivityState = {
    headline: text(s.headline, "headline", true)!,
    status: s.status as LiveActivityState["status"],
    symbol: text(s.symbol, "symbol", true)!,
  };
  const subtitle = text(s.subtitle, "subtitle", false);
  const detail = text(s.detail, "detail", false);
  const countdownTo = text(s.countdownTo, "countdownTo", false);
  if (subtitle) out.subtitle = subtitle;
  if (detail) out.detail = detail;
  if (countdownTo) {
    const at = new Date(countdownTo);
    if (Number.isNaN(at.getTime())) throw new Error("Live Activity countdownTo must be an ISO 8601 time");
    out.countdownTo = at.toISOString();
  }
  if (s.progress !== undefined && s.progress !== null) {
    if (typeof s.progress !== "number" || !Number.isFinite(s.progress)) {
      throw new Error("Live Activity progress must be a number");
    }
    out.progress = Math.min(1, Math.max(0, s.progress));
  }
  return out;
}

export async function isSupported(): Promise<boolean> {
  try {
    return !!Native && Native.isSupported();
  } catch {
    return false;
  }
}

export async function start(app: string, state: LiveActivityState): Promise<LiveActivityHandle> {
  const native = need();
  const id = await native.start(owner(app), checked(state));
  return { id };
}

export async function update(app: string, id: string, state: LiveActivityState): Promise<void> {
  const native = need();
  owned(native, app, id);
  await native.update(id, checked(state));
}

export async function end(app: string, id: string): Promise<void> {
  const native = need();
  owned(native, app, id);
  await native.end(id);
}
