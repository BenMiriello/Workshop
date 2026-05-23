import { useCallback, useEffect, useState } from "react";

// Location app page — read-only view of operator position fed by Owntracks
// iOS. Web geolocation is gone; this page is a viewer + privacy controls.

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
  const [history, setHistory] = useState<LocationRecord[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const [lr, hr] = await Promise.all([
        fetch("/api/location/latest"),
        fetch("/api/location/history?limit=50"),
      ]);
      const lj = lr.ok ? await lr.json() : { latest: null };
      const hj = hr.ok ? await hr.json() : { history: [] };
      setLatest((lj.latest as LocationRecord) || null);
      setHistory((hj.history as LocationRecord[]) || []);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => { clearInterval(t); clearInterval(tick); };
  }, [load]);

  const onClear = async () => {
    if (!confirm("Erase ALL stored location history? This deletes the backend file.")) return;
    try {
      const r = await fetch("/api/location/history", { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <h2 style={{ margin: "0 0 1rem" }}>Location</h2>

      {err && <div className="err">{err}</div>}

      <div className="card">
        <strong>Latest position</strong>
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
            No position recorded yet. Install Owntracks iOS — see <code>docs/owntracks-ios.md</code>.
          </p>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.5rem" }}>
          <strong>History ({history.length})</strong>
          <button onClick={onClear} style={{ background: "var(--err)", color: "white", borderColor: "transparent" }}>
            Clear history
          </button>
        </div>
        {history.length === 0 ? (
          <p className="muted" style={{ marginTop: "0.5rem" }}>No history.</p>
        ) : (
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
                {history.map((r, i) => (
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
        )}
      </div>
    </div>
  );
}
