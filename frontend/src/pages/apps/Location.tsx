import { useEffect, useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import {
  AppSettings, Button, ConfirmDialog, Notice, PageHeader, Skeleton, StateBlock, StatusDot,
  backgroundTrackingSupported, isNativeShell, startDeviceTracking,
  stopDeviceTracking, timeAgo, type AppChatDockRenderer,
} from "@workshop/ui";
import LocationSettings from "./location/LocationSettings";
// Direct import (not via the ui barrel) so maplibre/pmtiles aren't pulled into
// every page that touches the design system.
import { Map, type LngLat } from "@workshop/ui/map";
// The app's own key registry, fetchers and shared reads. Every server read on
// this page goes through it: the keys are built in one place (so the settings
// panel below shares this page's cache entry rather than polling the same URL
// again) and the generated response types come with them. See ./location/
// queries.ts for why an app declares its own keys instead of using the
// platform registry.
import {
  fetchHistory, locationKeys, useClearLocationHistory, useLatestLocation,
  type LocationRecord,
} from "./location/queries";

// Location app page — read-only view of operator position fed by Owntracks
// iOS. Web geolocation is gone; this page is a viewer.

const PAGE_SIZE = 25;
// The map draws the full recent breadcrumb, not just the first table page.
// At OwnTracks "significant" density this spans many days; backend caps at 5000.
const MAP_TRACK_LIMIT = 2000;

// One frozen array for "no rows yet", so the memos below keep their identity
// across the 10s re-render tick. `?? []` would hand <Map> a fresh array every
// render and yank the camera.
const NO_RECORDS: LocationRecord[] = [];

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
      {/* A line, not a region: the card and its control are still on screen. */}
      {err && <Notice tone="error" detail={err}>Could not change tracking on this device.</Notice>}
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
  // HOW MANY ROWS THE TABLE IS SHOWING, and the only piece of paging state
  // left. The rows themselves, the total, "is there more" and "is a read in
  // flight" were four more `useState`s kept in step by hand; every one of them
  // is now a field on the query below.
  const [shown, setShown] = useState(PAGE_SIZE);
  const [confirmClear, setConfirmClear] = useState(false);
  const [, setTick] = useState(0);

  // Polled, and SHARED with the settings panel at the bottom of this page: one
  // key, one cache entry, one value, so the two places on this screen that
  // quote the position cannot disagree. See ./location/queries.ts.
  const latestQ = useLatestLocation();
  const latest = latestQ.data ?? null;

  // THE TABLE: A GROWING WINDOW, not a stack of appended pages.
  //
  // "Load more" raises `shown`, which changes the key, which re-reads. That is
  // one cache entry holding one consistent answer, instead of an array grown
  // by hand from responses that could arrive in any order. It also fixes a
  // real defect in the paging it replaces: offset paging over a NEWEST-FIRST
  // list shifts under you, so every ping that landed mid-session made the next
  // page repeat a row that had already been drawn (invisible, because the rows
  // were keyed by index). A prefix of the current list cannot do that.
  //
  // The previous window stays on screen while the wider one is in flight
  // (`keepPreviousData`), so clicking "load more" does not blank the table.
  const historyQ = useQuery({
    queryKey: locationKeys.history({ limit: shown, offset: 0 }),
    queryFn: () => fetchHistory({ limit: shown, offset: 0 }),
    placeholderData: keepPreviousData,
  });

  // THE MAP'S TRACK, AND WHY IT IS STILL ITS OWN READ.
  //
  // This is the same route as the table's read, at the same offset, and the
  // table's 25 rows are a strict PREFIX of these 2000 -- so in principle the
  // table could be a slice of this one array and the page could read the route
  // once. It is deliberately not, for two reasons:
  //
  //  1. The table would then not paint until the whole breadcrumb landed. That
  //     is hundreds of kilobytes and a much slower backend query, for a table
  //     whose first screen is 25 rows.
  //  2. A failed track has to degrade the MAP only. Merged, one slow or failed
  //     request would take the history list down with it.
  //
  // What they do share is the key family, so the clear-history write drops both
  // with one prefix invalidation, and neither can hold a value the other has
  // already superseded.
  const trackQ = useQuery({
    queryKey: locationKeys.history({ limit: MAP_TRACK_LIMIT, offset: 0 }),
    queryFn: () => fetchHistory({ limit: MAP_TRACK_LIMIT, offset: 0 }),
  });

  // One writer, shared with the settings panel, so the two cannot invalidate
  // different things after the same DELETE.
  const clear = useClearLocationHistory();

  const records = historyQ.data?.items ?? NO_RECORDS;
  const trackRecords = trackQ.data?.items ?? NO_RECORDS;
  const total = historyQ.data?.page?.total ?? null;
  const hasMore = total != null && records.length < total;

  // Relative "last fix" label re-render, not a server read: pausing it while
  // hidden is harmless and it is not what the polls are for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => clearInterval(tick);
  }, []);

  // Breadcrumb segmented by time/teleport gaps so quiet stretches don't draw as
  // long straight lines. Use the dedicated larger track fetch; fall back to the
  // table page if it is empty or failed.
  // Memoize so the 10s re-render tick doesn't hand <Map> fresh array refs every
  // render (which would refire its sync effect and yank the camera).
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

      <div className="card">
        <strong>Latest</strong>
        {latestQ.isPending ? (
          <div style={{ marginTop: "0.5rem" }}><Skeleton width="18rem" /></div>
        ) : latest ? (
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
        ) : latestQ.isError ? (
          // A REGION, because this space would hold the position if the read
          // had worked. It is NOT the empty state below: the route answers 200
          // with a null body when nothing has ever been recorded, so "nothing
          // yet" and "could not load" are different answers and the card now
          // says which one it got.
          <StateBlock
            variant="error"
            title="Couldn't load the latest position."
            hint="The request to /api/location/latest failed."
            action={<Button size="sm" onClick={() => void latestQ.refetch()}>Retry</Button>}
          />
        ) : (
          <StateBlock
            title="No position recorded yet"
            hint="Set up the Owntracks app on your phone to start reporting."
          />
        )}
        {/* A dropped poll UNDER a position that is still on screen is a line,
            not a region: the reader keeps the last fix and its age. */}
        {latest && latestQ.isError && (
          <Notice
            tone="warn"
            action={<Button size="sm" variant="ghost" onClick={() => void latestQ.refetch()}>Retry</Button>}
          >
            Could not refresh the latest position.
          </Notice>
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
        {/* The fallback to the table's rows was silent before, so a failed
            track drew a short line that looked like a real one. */}
        {trackQ.isError && (
          <Notice
            tone="warn"
            action={<Button size="sm" variant="ghost" onClick={() => void trackQ.refetch()}>Retry</Button>}
          >
            Could not load the full breadcrumb. The map is drawing the rows below instead.
          </Notice>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.5rem" }}>
          <strong>History {total != null ? `(${records.length} of ${total})` : `(${records.length})`}</strong>
          <Button variant="danger" onClick={() => { clear.reset(); setConfirmClear(true); }}>
            Clear all
          </Button>
        </div>
        {historyQ.isPending ? (
          <div style={{ marginTop: "0.6rem", display: "grid", gap: "0.4rem" }}>
            <Skeleton width="100%" />
            <Skeleton width="100%" />
            <Skeleton width="100%" />
          </div>
        ) : records.length === 0 ? (
          historyQ.isError ? (
            <StateBlock
              variant="error"
              title="Couldn't load history."
              hint="The request to /api/location/history failed."
              action={<Button size="sm" onClick={() => void historyQ.refetch()}>Retry</Button>}
            />
          ) : (
            <StateBlock
              title="No history yet"
              hint="Positions appear here as soon as the phone reports one."
            />
          )
        ) : (
          <>
            {/* Rows are still on screen, so a failed re-read is a line. */}
            {historyQ.isError && (
              <Notice
                tone="warn"
                action={<Button size="sm" variant="ghost" onClick={() => void historyQ.refetch()}>Retry</Button>}
              >
                Could not refresh the history.
              </Notice>
            )}
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
                  {records.map((r) => (
                    // Keyed by the fix itself, not by its index: the window
                    // grows and re-reads, so an index key would let React reuse
                    // a row for a different position.
                    <tr key={`${r.ts}-${r.lat}-${r.lng}`} style={{ borderBottom: "1px solid var(--border)" }}>
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
                <Button
                  variant="ghost"
                  loading={historyQ.isFetching}
                  onClick={() => setShown((n) => n + PAGE_SIZE)}
                >
                  {`Load ${Math.min(PAGE_SIZE, (total ?? Infinity) - records.length)} more`}
                </Button>
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
        onConfirm={() => clear.mutate(undefined, { onSuccess: () => setConfirmClear(false) })}
        title="Erase all stored location history?"
        body={
          <>
            Deletes every position ping Workshop has stored, including the map track. This
            can't be undone.
            {/* The form is right here, so a rejected submit is a line inside it,
                never a region (frontend/CLAUDE.md, "A region or a line"). */}
            {clear.isError && (
              <Notice tone="error" detail={clear.error.message}>Nothing was deleted.</Notice>
            )}
          </>
        }
        confirmLabel="Erase history"
        busy={clear.isPending}
      />

      {/* In-app assistant — defaults to location scope (knows last ping + age);
          operator can swap providers from the picker inside the dock. */}
      {renderChatDock?.({ title: "Location assistant", placeholder: "Ask about latest position…" })}
    </div>
  );
}
