// Live Activity (lock screen + Dynamic Island) — the commute countdown.
//
// The LOCAL-update version is FREE: ActivityKit lets the app start/update an
// activity whenever it is running (foreground, or background while a location
// session keeps it alive). The native side lives in the optional Expo module
// `workshop-live-activity` (modules/workshop-live-activity), which only exists
// once a custom dev build includes the WidgetKit extension. Until then every
// method here degrades gracefully so phases 1-3b/3d work without it.
//
// Push-DRIVEN updates (server updates the activity while the app is fully
// asleep) are the deferred PAID feature and are not wired here.

import { requireOptionalNativeModule } from "expo-modules-core";

import type { CommuteActivityState, LiveActivityHandle } from "@shared/native-bridge";

interface NativeLiveActivity {
  isSupported(): boolean;
  start(state: CommuteActivityState): Promise<string>;
  update(id: string, state: CommuteActivityState): Promise<void>;
  end(id: string): Promise<void>;
}

const Native = requireOptionalNativeModule<NativeLiveActivity>("WorkshopLiveActivity");

export async function isSupported(): Promise<boolean> {
  try {
    return !!Native && Native.isSupported();
  } catch {
    return false;
  }
}

export async function start(state: CommuteActivityState): Promise<LiveActivityHandle> {
  if (!Native) throw new Error("Live Activities not available in this build");
  const id = await Native.start(state);
  return { id };
}

export async function update(id: string, state: CommuteActivityState): Promise<void> {
  if (!Native) return;
  await Native.update(id, state);
}

export async function end(id: string): Promise<void> {
  if (!Native) return;
  await Native.end(id);
}
