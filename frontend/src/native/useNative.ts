// React conveniences over the native bridge.

import { useEffect, useState } from "react";
import type { Capability } from "@shared/native-bridge";
import { native, hasCapability, isNativeShell } from "./bridge";

export interface NativeInfo {
  isShell: boolean;
  tier: "free" | "paid" | null;
  has: (cap: Capability) => boolean;
}

/** Snapshot of the native environment for conditional UI. */
export function useNative(): NativeInfo {
  // The bridge is installed before page scripts run, so this is stable for the
  // lifetime of the document; no subscription needed.
  const n = native();
  return {
    isShell: isNativeShell(),
    tier: n ? n.tier : null,
    has: hasCapability,
  };
}

/** Subscribe to a native event topic with automatic cleanup. */
export function useNativeEvent<T = unknown>(
  topic: Parameters<NonNullable<ReturnType<typeof native>>["on"]>[0],
  cb: (payload: T) => void,
): void {
  useEffect(() => {
    const n = native();
    if (!n) return;
    return n.on<T>(topic, cb);
    // cb is intentionally not a dep — callers pass stable handlers or accept the
    // first-render closure, matching how the rest of the app wires effects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic]);
}

/** Re-render-friendly tracking status poller (only meaningful in the shell). */
export function useTrackingStatus(pollMs = 5000) {
  const [status, setStatus] = useState<Awaited<
    ReturnType<NonNullable<ReturnType<typeof native>>["location"]["trackingStatus"]>
  > | null>(null);

  useEffect(() => {
    const n = native();
    if (!n) return;
    let alive = true;
    const tick = () =>
      n.location
        .trackingStatus()
        .then((s) => alive && setStatus(s))
        .catch(() => {});
    tick();
    const h = setInterval(tick, pollMs);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [pollMs]);

  return status;
}
