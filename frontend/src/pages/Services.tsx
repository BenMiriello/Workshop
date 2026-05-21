import { useEffect, useState, useCallback } from "react";

type ServiceStatus = {
  name: string;
  category: string;
  url: string;
  probe_path: string;
  status: "up" | "degraded" | "down";
  http_status: number | null;
  latency_ms: number;
  error?: string;
  notes?: string;
};

type ServicesResp = {
  services: ServiceStatus[];
  summary: { up: number; degraded: number; down: number };
};

const statusColor = (s: ServiceStatus["status"]) =>
  s === "up" ? "#16a34a" : s === "degraded" ? "#f59e0b" : "#ef4444";

// Backend reports service URLs as 127.0.0.1 (its own loopback). From the
// browser, rewrite to the host the user is actually viewing Workshop from
// so the link resolves to the same machine across LAN/tailnet/localhost.
const externalUrl = (url: string, probe: string) => {
  try {
    const u = new URL(url);
    if (u.hostname === "127.0.0.1" || u.hostname === "localhost") {
      u.hostname = window.location.hostname;
    }
    return u.toString().replace(/\/$/, "") + probe;
  } catch {
    return url + probe;
  }
};

export default function Services() {
  const [data, setData] = useState<ServicesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/services/");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!data) return <p>Probing services…</p>;

  const byCat = data.services.reduce<Record<string, ServiceStatus[]>>((acc, s) => {
    (acc[s.category] ||= []).push(s);
    return acc;
  }, {});

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted">
          <span style={{ color: "#16a34a" }}>● {data.summary.up} up</span>
          {data.summary.degraded > 0 && <> · <span style={{ color: "#f59e0b" }}>● {data.summary.degraded} degraded</span></>}
          {data.summary.down > 0 && <> · <span style={{ color: "#ef4444" }}>● {data.summary.down} down</span></>}
        </p>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>

      {Object.entries(byCat).map(([cat, items]) => (
        <section key={cat} style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "0.95rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", marginBottom: "0.5rem" }}>{cat}</h2>
          <ul className="cards">
            {items.map((s) => {
              const href = externalUrl(s.url, s.probe_path);
              return (
                <li key={s.name} className="card" style={{ padding: 0 }}>
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`Open ${href}`}
                    style={{
                      display: "block",
                      padding: "1rem",
                      color: "inherit",
                      textDecoration: "none",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <h3 style={{ margin: 0, fontSize: "1rem" }}>{s.name} ↗</h3>
                      <span style={{ color: statusColor(s.status), fontWeight: 600, fontSize: "0.875rem" }}>● {s.status}</span>
                    </div>
                    <p className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>{href}</p>
                    <p className="muted">
                      {s.http_status != null ? `HTTP ${s.http_status} · ` : ""}{s.latency_ms}ms
                    </p>
                    {s.error && <p className="err" style={{ fontSize: "0.8rem" }}>{s.error}</p>}
                  </a>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
