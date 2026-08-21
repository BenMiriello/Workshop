import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Play, Square } from "lucide-react";
import {
  AppSettings, Button, ConfirmDialog, PageHeader, StatusDot,
  backgroundTrackingSupported, isNativeShell, shortHttpError, startDeviceTracking,
  stopDeviceTracking, timeAgo, usePoll, type AppChatDockRenderer,
} from "@workshop/ui";
import LocationSettings from "./location/LocationSettings";
// Direct import (not via the ui barrel) so maplibre/pmtiles aren't pulled into
// every page that touches the design system.
import { Map, type LngLat } from "@workshop/ui/map";
// GENERATED backend response types (frontend/CLAUDE.md "API response types
// are GENERATED"). This is the one deliberately kept cross-boundary import in
// this file (Stage 4b): the type is a wire contract with the platform's own
// backend, not platform UI behaviour, and until an extracted app generates
// its own client from its own service (stage 7+) there is exactly one
// backend schema to describe the HTTP boundary every app talks to. Hand-
// writing a duplicate type here is the documented worse option (see
// frontend/CLAUDE.md, which names this file as the case that motivated
// generating types at all).
import type { components } from "../../api/schema.gen";

// Location app page — read-only view of operator position fed by Owntracks
// iOS. Web geolocation is gone; this page is a viewer.

// GENERATED, not hand-written. These come from the backend's own OpenAPI
// schema (`npm run gen:api-types`), which knows them because `routes/location`
// declares `response_model=` on /latest, /history and /update. The
// hand-written versions that used to sit here said `accuracy_m?: number` for
// four fields the backend actually serialises as `number | null` -- a shape
// the type asserted could never occur, on the one page whose whole job is
// reading those fields. That is the drift class this pipeline exists to turn
// into a compile error; see docs/platform-redesign/
// target-architecture-2026-08-17.md item 2.
//
// ADR 0023 envelope: {"items": [...], "page": {"limit", "next_cursor",
// "total", "offset"}} -- `Page[LocationRecord]` on the backend, which is why
// the generated name reads the way it does.
type LocationRecord = components["schemas"]["LocationRecord"];
type HistoryResp = components["schemas"]["Page_LocationRecord_"];

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

export default function Location({
  groupNav,
  renderChatDock,
}: {
  groupNav?: ReactNode;
  renderChatDock?: AppChatDockRenderer;
}) {
  const [latest, setLatest] = useState<LocationRecord | null>(null);
  const [records, setRecords] = useState<LocationRecord[]>([]);
  const [trackRecords, setTrackRecords] = useState<LocationRecord[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [, setTick] = useState(0);

  const loadLatest = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : null;
      setLatest((j as LocationRecord) || null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/location/history?limit=${PAGE_SIZE}&offset=0`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = (await r.json()) as HistoryResp;
      const items = j.items || [];
      setRecords(items);
      setTotal(j.page?.total ?? null);
      setHasMore(j.page?.total != null && (j.page.offset ?? 0) + items.length < j.page.total);
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
      setTrackRecords(j.items || []);
    } catch {
      /* map just falls back to the table page if this fails */
    }
  }, []);

  const loadMore = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/location/history?limit=${PAGE_SIZE}&offset=${records.length}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = (await r.json()) as HistoryResp;
      const items = j.items || [];
      setRecords((prev) => [...prev, ...items]);
      setTotal(j.page?.total ?? null);
      setHasMore(j.page?.total != null && (j.page.offset ?? 0) + items.length < j.page.total);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [records.length]);

  useEffect(() => { loadFirstPage(); loadTrack(); }, [loadFirstPage, loadTrack]);
  usePoll(loadLatest, 30_000);

  // Relative "last fix" label re-render, not a server read: pausing it while
  // hidden is harmless and it is not what usePoll is for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => clearInterval(tick);
  }, []);

  const onClear = async () => {
    setClearing(true);
    try {
      const r = await fetch("/api/location/history", { method: "DELETE" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      await Promise.all([loadLatest(), loadFirstPage(), loadTrack()]);
      setConfirmClear(false);
    } catch (e) {
      setErr(String(e));
    } finally {
      setClearing(false);
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

  // Freshness verdict — recomputed each render so the 10s tick keeps it current.
  // >30min with no new ping means the tracker likely stalled (OwnTracks does this
  // on iOS); show it as stale instead of implying it's the live position.
  const latestStale =
    latest?.ts != null && Date.now() - Date.parse(latest.ts) > 30 * 60 * 1000;

  return (
    <div>
      <PageHeader title="Location" actions={groupNav} />
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
            <span className="muted">status</span>
            <span>
              {latestStale ? (
                <StatusDot status="warn">stale, last known, may not be current</StatusDot>
              ) : (
                <StatusDot status="ok">live</StatusDot>
              )}
            </span>
            <span className="muted">when</span><span>{timeAgo(latest.ts)} <span className="muted">({latest.ts})</span></span>
            <span className="muted">coordinates</span><span style={{ fontFamily: "ui-monospace, monospace" }}>{latest.lat.toFixed(5)}, {latest.lng.toFixed(5)}</span>
            {latest.accuracy_m != null && (<><span className="muted">accuracy</span><span>{Math.round(latest.accuracy_m)} m</span></>)}
            {latest.speed_mps != null && (<><span className="muted">speed</span><span>{latest.speed_mps.toFixed(1)} m/s ({(latest.speed_mps * 2.237).toFixed(1)} mph)</span></>)}
            {latest.heading_deg != null && (<><span className="muted">heading</span><span>{Math.round(latest.heading_deg)}°</span></>)}
            {latest.source && (<><span className="muted">source</span><span style={{ fontFamily: "ui-monospace, monospace" }}>{latest.source}</span></>)}
          </div>
        ) : (
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            No position recorded yet. Set up the Owntracks app on your phone to start reporting.
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
          {segments.length === 1 ? "" : "s"}. The phone only reports on real movement, so
          the line breaks into a new segment across a quiet gap rather than guessing a
          straight path. Switch it to "move" mode for a denser trail, at more battery.
        </p>
      </div>

      <div className="card">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.5rem" }}>
          <strong>History {total != null ? `(${records.length} of ${total})` : `(${records.length})`}</strong>
          <Button variant="danger" onClick={() => setConfirmClear(true)}>
            Clear all
          </Button>
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
                      <td style={{ padding: "0.3rem 0.5rem", color: "var(--muted)" }}>{timeAgo(r.ts)}</td>
                      <td style={{ padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace" }}>
                        {r.lat.toFixed(5)}, {r.lng.toFixed(5)}
                      </td>
                      <td style={{ padding: "0.3rem 0.5rem" }}>{r.accuracy_m != null ? Math.round(r.accuracy_m) : "–"}</td>
                      <td style={{ padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace", color: "var(--muted)" }}>
                        {r.source || "–"}
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
      <AppSettings>
        <LocationSettings />
      </AppSettings>

      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={onClear}
        title="Erase all stored location history?"
        body="Deletes every position ping Workshop has stored, including the map track. This can't be undone."
        confirmLabel="Erase history"
        busy={clearing}
      />

      {/* In-app assistant — defaults to location scope (knows last ping + age);
          operator can swap providers from the picker inside the dock. */}
      {renderChatDock?.({ title: "Location assistant", placeholder: "Ask about latest position…" })}
    </div>
  );
}
