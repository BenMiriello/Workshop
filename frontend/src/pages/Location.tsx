import { useEffect, useState } from "react";
import { useLocationSharing, type LocationRecord } from "../useLocationSharing";

// Workshop Location tab — controls operator location sharing into the local
// backend. Stays on this box only (no third-party services). The commute app
// + LLM read /api/location/latest to answer "am I on the train yet" etc.

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

const fmtCoord = (n: number, places = 5) => n.toFixed(places);

export default function Location() {
  const { status, latest, error, isSharing, updateOnce, startSharing, stopSharing, clearHistory, fetchHistory } =
    useLocationSharing({ source: "workshop" });
  const [history, setHistory] = useState<LocationRecord[]>([]);
  const [, setTick] = useState(0);

  // Refresh "Nm ago" labels every 10s without re-fetching.
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => clearInterval(t);
  }, []);

  const refreshHistory = async () => {
    setHistory(await fetchHistory(50));
  };

  useEffect(() => {
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onClear = async () => {
    if (!confirm("Erase all stored location history? Backend file will be deleted.")) return;
    await clearHistory();
    await refreshHistory();
  };

  return (
    <div>
      <h2 style={{ margin: "0 0 0.5rem" }}>Location</h2>
      <p style={{ color: "var(--muted)", marginTop: 0, fontSize: "0.9rem" }}>
        Share this browser's geolocation with the Workshop backend so the commute
        layer and the LLM can answer location-aware questions ("am I on the train
        yet," "did I leave for the station"). Data stays on this box — no third
        parties. Toggle off any time; clear history to wipe stored pings.
      </p>

      {error && (
        <div className="card" style={{ borderColor: "var(--danger,red)", color: "var(--danger,red)", fontSize: "0.9rem" }}>
          {error}
          {status === "denied" && (
            <div style={{ marginTop: "0.4rem", color: "var(--muted)", fontSize: "0.85rem" }}>
              iOS: Settings → Safari → Location → "debian.tail38e62e.ts.net" → Allow,
              then reload this page.
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
          <StatusPill status={status} />
          <span style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            {isSharing ? "browser sending position pings (~30s)" : "not sharing"}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
            {!isSharing && (
              <button
                onClick={startSharing}
                style={{
                  background: "var(--accent)",
                  color: "white",
                  border: "none",
                  padding: "0.45rem 1rem",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
              >
                Start sharing
              </button>
            )}
            {isSharing && (
              <button
                onClick={stopSharing}
                style={{
                  background: "var(--danger,#c53030)",
                  color: "white",
                  border: "none",
                  padding: "0.45rem 1rem",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
              >
                Stop sharing
              </button>
            )}
            <button onClick={updateOnce} style={{ padding: "0.45rem 0.9rem" }}>
              Update once
            </button>
          </div>
        </div>

        <hr style={{ borderColor: "var(--border)", margin: "0.9rem 0" }} />

        <strong>Latest position</strong>
        {latest ? (
          <div
            style={{
              marginTop: "0.4rem",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "0.25rem 0.9rem",
              fontSize: "0.92rem",
            }}
          >
            <span style={{ color: "var(--muted)" }}>updated</span>
            <span>
              {fmtAge(latest.ts)} <span style={{ color: "var(--muted)" }}>({latest.ts})</span>
            </span>
            <span style={{ color: "var(--muted)" }}>coordinates</span>
            <span style={{ fontFamily: "ui-monospace, monospace" }}>
              {fmtCoord(latest.lat)}, {fmtCoord(latest.lng)}
            </span>
            {latest.accuracy_m != null && (
              <>
                <span style={{ color: "var(--muted)" }}>accuracy</span>
                <span>{Math.round(latest.accuracy_m)} m</span>
              </>
            )}
            {latest.speed_mps != null && (
              <>
                <span style={{ color: "var(--muted)" }}>speed</span>
                <span>
                  {latest.speed_mps.toFixed(1)} m/s ({(latest.speed_mps * 2.237).toFixed(1)} mph)
                </span>
              </>
            )}
            {latest.heading_deg != null && (
              <>
                <span style={{ color: "var(--muted)" }}>heading</span>
                <span>{Math.round(latest.heading_deg)}°</span>
              </>
            )}
            {latest.source && (
              <>
                <span style={{ color: "var(--muted)" }}>source</span>
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{latest.source}</span>
              </>
            )}
          </div>
        ) : (
          <p style={{ color: "var(--muted)", marginTop: "0.4rem" }}>
            No position recorded yet. Hit "Update once" or "Start sharing".
          </p>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <strong>History ({history.length})</strong>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button onClick={refreshHistory}>Refresh</button>
            <button
              onClick={onClear}
              style={{ background: "var(--danger,#c53030)", color: "white", borderColor: "transparent" }}
            >
              Clear history
            </button>
          </div>
        </div>
        {history.length === 0 ? (
          <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>No history.</p>
        ) : (
          <div className="table-scroll" style={{ marginTop: "0.6rem" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                  <th style={{ padding: "0.35rem 0.5rem" }}>when</th>
                  <th style={{ padding: "0.35rem 0.5rem" }}>lat, lng</th>
                  <th style={{ padding: "0.35rem 0.5rem" }}>acc (m)</th>
                  <th style={{ padding: "0.35rem 0.5rem" }}>spd (m/s)</th>
                  <th style={{ padding: "0.35rem 0.5rem" }}>source</th>
                </tr>
              </thead>
              <tbody>
                {history.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "0.3rem 0.5rem", color: "var(--muted)" }}>{fmtAge(r.ts)}</td>
                    <td style={{ padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace" }}>
                      {fmtCoord(r.lat)}, {fmtCoord(r.lng)}
                    </td>
                    <td style={{ padding: "0.3rem 0.5rem" }}>{r.accuracy_m != null ? Math.round(r.accuracy_m) : "—"}</td>
                    <td style={{ padding: "0.3rem 0.5rem" }}>{r.speed_mps != null ? r.speed_mps.toFixed(1) : "—"}</td>
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

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; label: string }> = {
    idle: { bg: "var(--muted)", label: "idle" },
    requesting: { bg: "var(--warn,#b7791f)", label: "requesting…" },
    sharing: { bg: "var(--accent)", label: "sharing" },
    denied: { bg: "var(--danger,#c53030)", label: "permission denied" },
    error: { bg: "var(--danger,#c53030)", label: "error" },
  };
  const m = map[status] || map.idle;
  return (
    <span
      style={{
        background: m.bg,
        color: "white",
        padding: "0.18rem 0.65rem",
        borderRadius: "999px",
        fontSize: "0.78rem",
        fontWeight: 600,
      }}
    >
      {m.label}
    </span>
  );
}
