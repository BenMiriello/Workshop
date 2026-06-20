// Web-side accessor for the Workshop native bridge.
//
// When the web app runs inside the Expo shell, the shell injects
// `window.WorkshopNative` (see mobile/native/inject.ts) before this code loads.
// In a plain browser it is absent. Everything here is null-safe so the same
// component renders in both places — call `native()?.location...` and provide a
// web fallback when it returns undefined.

import type {
  Capability,
  WorkshopNative,
  BridgeEventTopic,
} from "@shared/native-bridge";

declare global {
  interface Window {
    WorkshopNative?: WorkshopNative;
  }
}

/** The native bridge, or undefined in a plain browser. */
export function native(): WorkshopNative | undefined {
  return typeof window !== "undefined" ? window.WorkshopNative : undefined;
}

/** True when running inside the Workshop mobile shell. */
export function isNativeShell(): boolean {
  return !!native();
}

/** True when a specific capability is available right now. */
export function hasCapability(cap: Capability): boolean {
  const n = native();
  return !!n && n.has(cap);
}

/** Whether paid-tier entitlements (remote push / push Live Activities) are present. */
export function isPaidTier(): boolean {
  return native()?.tier === "paid";
}

/**
 * Subscribe to a pushed native event (e.g. a background location fix). Returns
 * an unsubscribe function; no-op when not in the shell.
 */
export function onNativeEvent<T = unknown>(
  topic: BridgeEventTopic,
  cb: (payload: T) => void,
): () => void {
  const n = native();
  if (!n) return () => {};
  return n.on<T>(topic, cb);
}

export type { Capability, WorkshopNative, BridgeEventTopic };
