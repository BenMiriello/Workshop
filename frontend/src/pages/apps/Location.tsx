import { useCallback, useEffect, useState } from "react";
import ChatDock from "../../components/ChatDock";
import { AppGroupNav } from "../../components/appNav";
import { PageHeader } from "../../ui";
// Direct import (not via the ui barrel) so maplibre/pmtiles aren't pulled into
// every page that touches the design system.
import { Map, type LngLat } from "../../ui/Map";

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

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export default function Location() {
  const [latest, setLatest] = useState<LocationRecord | null>(null);
  const [records, setRecords] = useState<LocationRecord[]>([]);
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
    const t = setInterval(loadLatest, 30_000);
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => { clearInterval(t); clearInterval(tick); };
  }, [loadLatest, loadFirstPage]);

  const onClear = async () => {
    if (!confirm("Erase ALL stored location history? This deletes the backend file.")) return;
    try {
      const r = await fetch("/api/location/history", { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await Promise.all([loadLatest(), loadFirstPage()]);
    } catch (e) {
      setErr(String(e));
    }
  };

  // Breadcrumb in chronological order (history is newest-first); marker = latest.
  const track: LngLat[] = records.map((r) => [r.lng, r.lat] as LngLat).reverse();
  const marker: LngLat | null = latest ? [latest.lng, latest.lat] : null;

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

      <div className="card">
        <strong>Map</strong>
        <div style={{ marginTop: "0.5rem" }}>
          <Map track={track} marker={marker} height={380} />
        </div>
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
      <ChatDock contextKey="location" title="Location assistant" placeholder="Ask about latest position…" />
    </div>
  );
}
