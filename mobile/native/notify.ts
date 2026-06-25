// Local (on-device) notifications. FREE — no APNs, no paid program. Used for
// schedule-derived commute alerts ("leave by 7:11"); the web app computes the
// fire time and we hand it to iOS, which shows it even if the app never runs
// again. Remote/server push is the deferred paid feature (see services/push).

import * as Notifications from "expo-notifications";

import type { LocalNotification, PermissionState } from "@shared/native-bridge";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

function mapPermission(status: string): PermissionState {
  if (status === "granted") return "granted";
  if (status === "denied") return "denied";
  return "undetermined";
}

async function ensurePermission(): Promise<boolean> {
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const req = await Notifications.requestPermissionsAsync();
  return req.granted;
}

export async function permissionStatus(): Promise<PermissionState> {
  const p = await Notifications.getPermissionsAsync();
  return mapPermission(p.status);
}

/** Explicitly prompt for permission (the "Enable notifications" control). */
export async function requestPermission(): Promise<PermissionState> {
  await ensurePermission();
  return permissionStatus();
}

export async function schedule(n: LocalNotification): Promise<void> {
  if (!(await ensurePermission())) throw new Error("notification permission denied");

  // Re-scheduling the same logical alert replaces the prior one.
  await Notifications.cancelScheduledNotificationAsync(n.id).catch(() => {});

  const when = new Date(n.fireAt);
  const fireDate = when.getTime() > Date.now() ? when : new Date(Date.now() + 1000);

  await Notifications.scheduleNotificationAsync({
    identifier: n.id,
    content: { title: n.title, body: n.body, data: n.data ?? {} },
    trigger: { date: fireDate },
  });
}

export async function cancel(id: string): Promise<void> {
  await Notifications.cancelScheduledNotificationAsync(id).catch(() => {});
}

export async function cancelAll(): Promise<void> {
  await Notifications.cancelAllScheduledNotificationsAsync();
}
