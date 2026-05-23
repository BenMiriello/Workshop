import { useCallback, useEffect, useState } from "react";
import Config from "./Config";
import { ArrowRight, ExternalLink } from "lucide-react";

// Commute app — day-of status + config. Reads /api/commute/* on the Workshop
// backend. Lives on its own Tailscale Serve port; each Workshop app is a
// peer at its own HTTPS origin.

type Leg = {
  step: number;
  mode: string;
  from: string;
  to: string;
  default_minutes: number;
  preferred_train?: string;
  preferred_route?: string;
  source_keys?: string[];
};

type ServiceAlert = {
  source: string;
  header: string;
  description: string;
  effect?: number | string;
};

type Brief = {
  ts: string;
  ts_local: string;
  active: boolean;
  reason?: string;
  schedule_id?: string;
  direction?: "to-work" | "to-home";
  target_arrive?: string;
  target_arrive_local?: string;
  flex_minutes_late?: number;
  flex_deadline?: string;
  flex_deadline_local?: string;
  projected_arrive?: string;
  projected_arrive_local?: string;
  expected_leg_index?: number;
  expected_leg?: Leg | null;
  leg_count?: number;
  rt_delay_min?: number;
  late_risk?: boolean;
  late_by_min?: number;
  alerts?: ServiceAlert[];
  legs?: Leg[];
};

type AlertRecord = {
  ts: string;
  schedule_id: string;
  day: string;
  late_by_min?: number;
  projected_arrive_local?: string;
  delivery?: Record<string, unknown>;
  force?: boolean;
  reason?: string;
};

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

const API = "/api/commute";

type Tab = "status" | "config";

const WORKSHOP_URL = "https://debian.tail38e62e.ts.net:8443/";
const OW_URL = "https://debian.tail38e62e.ts.net:8445/";
const LAUNCHER_URL = "https://debian.tail38e62e.ts.net/";

export default function App() {
  const [tab, setTab] = useState<Tab>("status");

  return (
    <div className="app">
      <header className="system-nav">
        <a className="brand" href={LAUNCHER_URL} title="Launcher">
          🏠
        </a>
        <h1 className="app-name">Commute</h1>
        <nav className="app-tabs">
          {(["status", "config"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={tab === t ? "tab active" : "tab"}
            >
              {t}
            </button>
          ))}
        </nav>
        <nav className="peer-apps">
          <a href={WORKSHOP_URL}>Workshop <ExternalLink size={11} strokeWidth={1.8} aria-hidden /></a>
          <a href={OW_URL}>Wearables <ExternalLink size={11} strokeWidth={1.8} aria-hidden /></a>
        </nav>
      </header>

      {tab === "status" ? <StatusView /> : <Config />}
    </div>
  );
}

function StatusView() {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [b, a] = await Promise.all([
        fetch(`${API}/status`),
        fetch(`${API}/alerts/recent?limit=10`),
      ]);
      if (b.ok) setBrief(await b.json());
      if (a.ok) {
        const j = await a.json();
        setAlerts(j.alerts || []);
      }
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const t = setInterval(fetchAll, 30_000);
    return () => clearInterval(t);
  }, [fetchAll]);

  const triggerPoll = async () => {
    setPolling(true);
    try {
      await fetch(`${API}/poll`, { method: "POST" });
      await fetchAll();
    } finally {
      setPolling(false);
    }
  };

  return (
    <>
      <div style={{ marginBottom: "0.75rem" }}>
        <span className="muted">{brief ? `updated ${fmtAge(brief.ts)}` : "loading…"}</span>
      </div>

      {err && <div className="err">{err}</div>}

      <StatusCard brief={brief} />

      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
        <button onClick={triggerPoll} disabled={polling}>
          {polling ? "polling…" : "Poll now"}
        </button>
      </div>

      <AlertsList alerts={alerts} />
    </>
  );
}

function StatusCard({ brief }: { brief: Brief | null }) {
  if (!brief) return <div className="card muted">loading status…</div>;

  if (!brief.active) {
    return (
      <div className="card">
        <span className="status-banner inactive">inactive</span>
        <p className="muted" style={{ marginTop: "0.6rem" }}>
          No active schedule right now.{brief.reason ? ` (${brief.reason})` : ""}
        </p>
        {brief.alerts && brief.alerts.length > 0 && (
          <div style={{ marginTop: "0.8rem" }}>
            <strong>Service alerts on route</strong>
            <ul>
              {brief.alerts.map((a, i) => (
                <li key={i}><span className="muted">[{a.source}]</span> {a.header}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const risk = !!brief.late_risk;
  const directionTarget = brief.direction === "to-work" ? "work" : "home";

  return (
    <div className="card" style={{ borderLeft: `5px solid ${risk ? "var(--danger)" : "var(--ok)"}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "0.5rem" }}>
        <h2 style={{ margin: 0, fontSize: "1.15rem", display: "inline-flex", alignItems: "center", gap: "0.4rem", flexWrap: "wrap" }}>
          {brief.schedule_id}
          <span className="muted" style={{ fontWeight: 400, display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
            · <ArrowRight size={14} strokeWidth={1.8} aria-hidden /> {directionTarget}
          </span>
        </h2>
        <span className={`status-banner ${risk ? "risk" : "ok"}`}>
          {risk ? `late · +${Math.round(brief.late_by_min || 0)} min` : "on track"}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0.3rem 1rem", marginTop: "0.8rem", fontSize: "0.95rem" }}>
        <span className="muted">target arrive</span><span>{brief.target_arrive_local}</span>
        <span className="muted">flex deadline</span><span>{brief.flex_deadline_local} (+{brief.flex_minutes_late} min)</span>
        <span className="muted">projected</span>
        <span style={{ fontWeight: risk ? 600 : 400, color: risk ? "var(--danger)" : undefined }}>
          {brief.projected_arrive_local}
        </span>
        <span className="muted">realtime delay</span><span>{brief.rt_delay_min ? `${brief.rt_delay_min} min` : "—"}</span>
      </div>

      <hr style={{ borderColor: "var(--border)", margin: "0.9rem 0" }} />

      <strong>Legs ({brief.leg_count})</strong>
      <ol className="leg-list">
        {(brief.legs || []).map((leg, i) => {
          const active = i === brief.expected_leg_index;
          return (
            <li key={leg.step} className={active ? "active" : ""} style={{ display: "flex", alignItems: "center", gap: "0.25rem", flexWrap: "wrap" }}>
              <span className="muted" style={{ fontWeight: 400 }}>[{leg.mode}]</span>
              <span>{leg.from}</span>
              <ArrowRight size={12} strokeWidth={1.8} aria-hidden />
              <span>{leg.to}</span>
              <span className="muted" style={{ fontWeight: 400 }}>({leg.default_minutes} min)</span>
              {active && (
                <span style={{ marginLeft: "0.5rem", fontSize: "0.85rem" }}>← now</span>
              )}
            </li>
          );
        })}
      </ol>

      {brief.alerts && brief.alerts.length > 0 && (
        <>
          <hr style={{ borderColor: "var(--border)", margin: "0.9rem 0" }} />
          <strong>Service alerts</strong>
          <ul>
            {brief.alerts.map((a, i) => (
              <li key={i}><span className="muted">[{a.source}]</span> {a.header}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function AlertsList({ alerts }: { alerts: AlertRecord[] }) {
  if (alerts.length === 0) {
    return <div className="card muted">No late-risk alerts.</div>;
  }
  return (
    <div className="card">
      <strong>Recent late-risk alerts</strong>
      <ul style={{ paddingLeft: "1.2rem", marginTop: "0.5rem" }}>
        {alerts.slice().reverse().map((a, i) => (
          <li key={i} style={{ marginBottom: "0.35rem" }}>
            <span className="muted">{fmtAge(a.ts)}</span> — <strong>{a.schedule_id}</strong> · proj{" "}
            {a.projected_arrive_local} · +{Math.round(a.late_by_min || 0)} min
            {a.force && <span style={{ marginLeft: "0.5rem", color: "var(--warn)", fontSize: "0.85rem" }}>(forced)</span>}
            {a.reason && <div className="muted" style={{ fontSize: "0.85rem" }}>{a.reason}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}
