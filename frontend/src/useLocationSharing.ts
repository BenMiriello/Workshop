// Shared hook used by both the Workshop frontend (port 5173) and the Commute
// app (port 5174). Wraps the browser Geolocation API + posts to the backend.
//
// Behavior:
//  - One-shot: getCurrentPosition + POST when the operator hits "Update once".
//  - Continuous: navigator.geolocation.watchPosition + POST on every change.
//    Throttled to MIN_INTERVAL_MS so a quiet watch doesn't hammer the backend.
//  - "source" tag is passed in so the backend knows which app sent the ping.
//  - Privacy: only talks to /api on the same host (Vite proxies to backend).
//
// React import is hook-only so this file is consumable by either Vite app.

import { useCallback, useEffect, useRef, useState } from "react";

export type LocationRecord = {
  ts: string;
  lat: number;
  lng: number;
  accuracy_m?: number;
  altitude_m?: number;
  heading_deg?: number;
  speed_mps?: number;
  client_ts?: string;
  source?: string;
};

export type LocationStatus = "idle" | "requesting" | "sharing" | "denied" | "error";

const API_BASE = "/api/location";
const MIN_INTERVAL_MS = 25_000; // throttle floor between POSTs while watching

type Options = {
  source: string; // "workshop" | "commute-app"
};

export function useLocationSharing({ source }: Options) {
  const [status, setStatus] = useState<LocationStatus>("idle");
  const [latest, setLatest] = useState<LocationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const watchIdRef = useRef<number | null>(null);
  const lastPostMsRef = useRef<number>(0);

  // Pull latest known position from backend on mount so the UI shows continuity
  // even if the operator closed/reopened the page.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/latest`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!cancelled && j?.latest) setLatest(j.latest);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const postPosition = useCallback(
    async (pos: GeolocationPosition) => {
      const body = {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy_m: pos.coords.accuracy ?? undefined,
        altitude_m: pos.coords.altitude ?? undefined,
        heading_deg: pos.coords.heading ?? undefined,
        speed_mps: pos.coords.speed ?? undefined,
        client_ts: new Date(pos.timestamp).toISOString(),
        source,
      };
      try {
        const r = await fetch(`${API_BASE}/update`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const t = await r.text();
          throw new Error(`http ${r.status}: ${t}`);
        }
        const j = await r.json();
        if (j?.record) setLatest(j.record);
        lastPostMsRef.current = Date.now();
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    },
    [source],
  );

  const updateOnce = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setStatus("error");
      setError("Geolocation API not supported in this browser");
      return;
    }
    setStatus("requesting");
    setError(null);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        await postPosition(pos);
        // If we're not in continuous mode, fall back to idle. If we WERE
        // sharing, keep that state — updateOnce is a forced refresh.
        setStatus((s: LocationStatus) => (s === "sharing" ? "sharing" : "idle"));
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) setStatus("denied");
        else setStatus("error");
        const codeName = err.code === 1 ? "permission denied"
          : err.code === 2 ? "position unavailable (likely no GPS lock — try outside)"
          : err.code === 3 ? "timeout (GPS couldn't get a fix in 15s)"
          : `geolocation error code ${err.code}`;
        setError(`${codeName}${err.message ? `: ${err.message}` : ""}`);
      },
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 0 },
    );
  }, [postPosition]);

  const startSharing = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setStatus("error");
      setError("Geolocation API not supported in this browser");
      return;
    }
    if (watchIdRef.current !== null) return; // already sharing
    setStatus("requesting");
    setError(null);
    const id = navigator.geolocation.watchPosition(
      async (pos) => {
        setStatus("sharing");
        const now = Date.now();
        if (now - lastPostMsRef.current < MIN_INTERVAL_MS) return; // throttle
        await postPosition(pos);
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) setStatus("denied");
        else setStatus("error");
        const codeName = err.code === 1 ? "permission denied"
          : err.code === 2 ? "position unavailable (likely no GPS lock — try outside)"
          : err.code === 3 ? "timeout (GPS couldn't get a fix in 20s)"
          : `geolocation error code ${err.code}`;
        setError(`${codeName}${err.message ? `: ${err.message}` : ""}`);
      },
      // Watch mode uses non-high-accuracy so iOS Safari falls back to
      // WiFi/cell triangulation instead of waiting for a GPS lock indoors.
      // See apps/commute/src/useLocationSharing.ts for the same rationale.
      { enableHighAccuracy: false, timeout: 20_000, maximumAge: 30_000 },
    );
    watchIdRef.current = id;
  }, [postPosition]);

  const stopSharing = useCallback(() => {
    if (watchIdRef.current !== null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
      watchIdRef.current = null;
    }
    setStatus("idle");
  }, []);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        navigator.geolocation.clearWatch(watchIdRef.current);
        watchIdRef.current = null;
      }
    };
  }, []);

  const clearHistory = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/history`, { method: "DELETE" });
      if (!r.ok) throw new Error(`http ${r.status}`);
      setLatest(null);
      setError(null);
      return await r.json();
    } catch (e) {
      setError(String(e));
      return null;
    }
  }, []);

  const fetchHistory = useCallback(async (limit = 50): Promise<LocationRecord[]> => {
    try {
      const r = await fetch(`${API_BASE}/history?limit=${limit}`);
      if (!r.ok) throw new Error(`http ${r.status}`);
      const j = await r.json();
      return (j.history || []) as LocationRecord[];
    } catch (e) {
      setError(String(e));
      return [];
    }
  }, []);

  return {
    status,
    latest,
    error,
    isSharing: status === "sharing",
    updateOnce,
    startSharing,
    stopSharing,
    clearHistory,
    fetchHistory,
  };
}
