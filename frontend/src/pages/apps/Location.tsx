import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, Square } from "lucide-react";
import AppChatDock from "../../components/AppChatDock";
import { AppGroupNav } from "../../components/appNav";
import { Button, PageHeader } from "../../ui";
// Direct import (not via the ui barrel) so maplibre/pmtiles aren't pulled into
// every page that touches the design system.
import { Map, type LngLat } from "../../ui/Map";
import { isNativeShell } from "../../native/bridge";
import {
  backgroundTrackingSupported,
  startDeviceTracking,
  stopDeviceTracking,
} from "../../native/location";

// Location app page — read-only view of operator position fed by Owntracks
// iOS. Web geolocation is gone; this page is a viewer.

type LocationRecord = {
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

type HistoryResp = {
  history: LocationRecord[];
  total: number;
  has_more: boolean;
  offset: number;
  limit: number;
};

const PAGE_SIZE = 25;
// The map draws the full recent breadcrumb, not just the first table page.
// At OwnTracks "significant" density this spans many days; backend caps at 5000.
const MAP_TRACK_LIMIT = 2000;

// Breadcrumb segmentation tunables. A new line segment begins when consecutive
// fixes are too far apart in time, or imply an impossible speed — so a phone
// that went quiet for hours, or flushed an old buffered fix from far away, is
// NOT drawn as one long straight line across the map. (OwnTracks buffers fixes
// offline and delivers them late; we order by the true fix time, not receipt.)
const SEGMENT_GAP_MS = 15 * 60_000; // > 15 min between fixes => break the line
const SEGMENT_TELEPORT_KMH = 250; // implied speed above this => break (teleport)
const MAX_ACCURACY_M = 200; // drop wildly imprecise fixes before drawing

const fixTime = (r: LocationRecord) => Date.parse(r.client_ts || r.ts);

function haversineKm(a: LngLat, b: LngLat): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// Order fixes by true fix time, then cut the breadcrumb into gap-free segments.
function buildSegments(records: LocationRecord[]): LngLat[][] {
  const pts = records
    .filter((r) => r.accuracy_m == null || r.accuracy_m <= MAX_ACCURACY_M)
    .map((r) => ({ t: fixTime(r), p: [r.lng, r.lat] as LngLat }))
    .filter((x) => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t);

  const segments: LngLat[][] = [];
  let cur: LngLat[] = [];
  for (let i = 0; i < pts.length; i++) {
    if (i > 0) {
      const dtMs = pts[i].t - pts[i - 1].t;
      const km = haversineKm(pts[i - 1].p, pts[i].p);
      const kmh = dtMs > 0 ? km / (dtMs / 3_600_000) : Infinity;
      if (dtMs > SEGMENT_GAP_MS || kmh > SEGMENT_TELEPORT_KMH) {
        if (cur.length) segments.push(cur);
        cur = [];
      }
    }
    cur.push(pts[i].p);
  }
  if (cur.length) segments.push(cur);
  return segments;
}

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

// Reports THIS device's position to the backend. In the mobile shell this hands
// off to native background tracking (works while the app is suspended); in a
// browser it is a foreground-only share (stops when the tab is backgrounded).
function DeviceTrackingCard() {
  const [tracking, setTracking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inShell = isNativeShell();
  const trueBackground = backgroundTrackingSupported();

  const toggle = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (tracking) {
        await stopDeviceTracking();
        setTracking(false);
      } else {
        await startDeviceTracking({ mode: "active" });
        setTracking(true);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <strong>This device</strong>
      <p className="muted" style={{ marginTop: "0.4rem", fontSize: "0.88rem" }}>
        {trueBackground
          ? "Report this phone's position to the backend, continuously and in the background, replacing OwnTracks."
          : inShell
            ? "Native background tracking is not available in this build yet."
            : "Share this browser's position while this tab is open. Continuous background tracking needs the Workshop app."}
      </p>
      <div style={{ marginTop: "0.6rem", display: "flex", alignItems: "center", gap: "0.6rem" }}>
        <Button onClick={toggle} disabled={busy} variant={tracking ? "ghost" : "primary"}>
          {tracking ? <Square size={16} /> : <Play size={16} />}
          {tracking ? "Stop sharing" : trueBackground ? "Start background tracking" : "Share from this browser"}
        </Button>
        {tracking && (
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            {trueBackground ? "tracking in background" : "sharing while tab is open"}
          </span>
        )}
      </div>
      {err && <div className="err" style={{ marginTop: "0.5rem" }}>{err}</div>}
    </div>
  );
}

export default function Location() {
  const [latest, setLatest] = useState<LocationRecord | null>(null);
  const [records, setRecords] = useState<LocationRecord[]>([]);
  const [trackRecords, setTrackRecords] = useState<LocationRecord[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const loadLatest = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : { latest: null };
      setLatest((j.latest as LocationRecord) || null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/location/history?limit=${PAGE_SIZE}&offset=0`);
      if (!r.ok) throw new Error(await r.text());
      const j = (await r.json()) as HistoryResp;
      setRecords(j.history || []);
      setTotal(j.total ?? null);
      setHasMore(!!j.has_more);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // The map shows the whole recent breadcrumb, independent of table paging.
  const loadTrack = useCallback(async () => {
    try {
      const r = await fetch(`/api/location/history?limit=${MAP_TRACK_LIMIT}&offset=0`);
      if (!r.ok) return;
      const j = (await r.json()) as HistoryResp;
      setTrackRecords(j.history || []);
    } catch {
      /* map just falls back to the table page if this fails */
    }
  }, []);

  const loadMore = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/location/history?limit=${PAGE_SIZE}&offset=${records.length}`);
      if (!r.ok) throw new Error(await r.text());
      const j = (await r.json()) as HistoryResp;
      setRecords((prev) => [...prev, ...(j.history || [])]);
      setTotal(j.total ?? null);
      setHasMore(!!j.has_more);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [records.length]);

  useEffect(() => {
    loadLatest();
    loadFirstPage();
    loadTrack();
    const t = setInterval(loadLatest, 30_000);
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => { clearInterval(t); clearInterval(tick); };
  }, [loadLatest, loadFirstPage, loadTrack]);

  const onClear = async () => {
    if (!confirm("Erase ALL stored location history? This deletes the backend file.")) return;
    try {
      const r = await fetch("/api/location/history", { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await Promise.all([loadLatest(), loadFirstPage(), loadTrack()]);
    } catch (e) {
      setErr(String(e));
    }
  };

  // Breadcrumb segmented by time/teleport gaps so quiet stretches don't draw as
  // long straight lines. Use the dedicated larger track fetch; fall back to the
  // table page if empty.
  // Memoize so the 10s/30s re-render ticks don't hand <Map> fresh array refs
  // every render (which would refire its sync effect and yank the camera).
  const segments = useMemo(
    () => buildSegments(trackRecords.length ? trackRecords : records),
    [trackRecords, records],
  );
  const pointCount = useMemo(
    () => segments.reduce((n, s) => n + s.length, 0),
    [segments],
  );
  const marker: LngLat | null = useMemo(
    () => (latest ? [latest.lng, latest.lat] : null),
    [latest],
  );

  return (
    <div>
      <PageHeader title="Location" actions={<AppGroupNav />} />
      {err && <div className="err">{err}</div>}

      <div className="card">
        <strong>Latest</strong>
        {latest ? (
          <div
            style={{
              marginTop: "0.5rem",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "0.25rem 0.9rem",
              fontSize: "0.92rem",
            }}
          >
            <span className="muted">when</span><span>{fmtAge(latest.ts)} <span className="muted">({latest.ts})</span></span>
            <span className="muted">coordinates</span><span style={{ fontFamily: "ui-monospace, monospace" }}>{latest.lat.toFixed(5)}, {latest.lng.toFixed(5)}</span>
            {latest.accuracy_m != null && (<><span className="muted">accuracy</span><span>{Math.round(latest.accuracy_m)} m</span></>)}
            {latest.speed_mps != null && (<><span className="muted">speed</span><span>{latest.speed_mps.toFixed(1)} m/s ({(latest.speed_mps * 2.237).toFixed(1)} mph)</span></>)}
            {latest.heading_deg != null && (<><span className="muted">heading</span><span>{Math.round(latest.heading_deg)}°</span></>)}
            {latest.source && (<><span className="muted">source</span><span style={{ fontFamily: "ui-monospace, monospace" }}>{latest.source}</span></>)}
          </div>
        ) : (
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            No position recorded yet. Configure Owntracks iOS — see <code>docs/owntracks-ios.md</code>.
          </p>
        )}
      </div>

      <DeviceTrackingCard />

      <div className="card">
        <strong>Map</strong>
        <div style={{ marginTop: "0.5rem" }}>
          <Map track={segments} marker={marker} height={380} />
        </div>
        <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.82rem" }}>
          {pointCount} point{pointCount === 1 ? "" : "s"} in {segments.length} segment
          {segments.length === 1 ? "" : "s"}. The breadcrumb is recorded pings, not
          continuous tracking — OwnTracks "significant" mode only pings on meaningful
          movement. When it goes quiet for a while (or flushes a buffered fix from far
          away), the line breaks into a new segment instead of drawing a long straight
          line across the gap. Switch the phone to "move" mode for denser tracking
          (higher battery use). Nothing is deleted server-side.
        </p>
      </div>

      <div className="card">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.5rem" }}>
          <strong>History {total != null ? `(${records.length} of ${total})` : `(${records.length})`}</strong>
          <button onClick={onClear} style={{ background: "var(--err)", color: "white", borderColor: "transparent" }}>
            Clear all
          </button>
        </div>
        {records.length === 0 ? (
          <p className="muted" style={{ marginTop: "0.5rem" }}>No history.</p>
        ) : (
          <>
            <div className="table-scroll" style={{ marginTop: "0.6rem" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                    <th style={{ padding: "0.35rem 0.5rem" }}>when</th>
                    <th style={{ padding: "0.35rem 0.5rem" }}>lat, lng</th>
                    <th style={{ padding: "0.35rem 0.5rem" }}>acc (m)</th>
                    <th style={{ padding: "0.35rem 0.5rem" }}>source</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "0.3rem 0.5rem", color: "var(--muted)" }}>{fmtAge(r.ts)}</td>
                      <td style={{ padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace" }}>
                        {r.lat.toFixed(5)}, {r.lng.toFixed(5)}
                      </td>
                      <td style={{ padding: "0.3rem 0.5rem" }}>{r.accuracy_m != null ? Math.round(r.accuracy_m) : "—"}</td>
                      <td style={{ padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace", color: "var(--muted)" }}>
                        {r.source || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {hasMore && (
              <div style={{ marginTop: "0.7rem", display: "flex", justifyContent: "center" }}>
                <button onClick={loadMore} disabled={loading}>
                  {loading ? "Loading…" : `Load ${Math.min(PAGE_SIZE, (total ?? Infinity) - records.length)} more`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {/* In-app assistant — defaults to location scope (knows last ping + age);
          operator can swap providers from the picker inside the dock. */}
      <AppChatDock appId="location" title="Location assistant" placeholder="Ask about latest position…" />
    </div>
  );
}
