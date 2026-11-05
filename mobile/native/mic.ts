// The app's microphone permission, for the web UI (shared/native-bridge.ts
// `mic`). The web view's own getUserMedia cannot show the system prompt for a
// permission iOS has not asked about yet, nor open Settings once it is refused.
import { Audio } from "expo-av";
import { Linking } from "react-native";
import type { PermissionState } from "@shared/native-bridge";

function stateOf(perm: { granted: boolean; canAskAgain: boolean }): PermissionState {
  if (perm.granted) return "granted";
  return perm.canAskAgain ? "undetermined" : "denied";
}

export async function status(): Promise<PermissionState> {
  return stateOf(await Audio.getPermissionsAsync());
}

export async function request(): Promise<PermissionState> {
  return stateOf(await Audio.requestPermissionsAsync());
}

export async function openSettings(): Promise<void> {
  await Linking.openSettings();
}
