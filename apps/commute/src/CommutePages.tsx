import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Circle } from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

type LegRealtime = {
  available: boolean;
  delay_min?: number | null;
  reason?: string | null;
};

type Leg = {
  step: number | string;
  mode: string;
  from: string;
  to: string;
  depart_local?: string | null;
  arrive_local?: string | null;
  default_minutes?: number | null;
  status?: "before" | "current" | "past";
  realtime?: LegRealtime | null;
  preferred_train?: string | null;
  preferred_route?: string | null;
};

type Alert = { source: string; header: string; description?: string; effect?: number };

type Brief = {
  active: boolean;
  schedule_id?: string;
  direction?: string;
  target_arrive_local?: string;
  flex_deadline_local?: string;
  flex_minutes_late?: number;
  projected_arrive_local?: string;
  late_risk?: boolean;
  late_by_min?: number;
  leave_by_local?: string | null;
  timeline?: Leg[];
  alerts?: Alert[];
  realtime_status?: { available: boolean; reason?: string };
};

type SourceStatus = {
  status: string;
  ts?: string;
  entity_count?: number;
  note?: string;
};

type SourcesResp = {
  registry: Record<string, unknown>;
  status: Record<string, SourceStatus>;
};

type AlertEntry = {
  ts: string;
  kind?: string;
  schedule_id?: string;
  checkpoint?: string;
  late_by_min?: number | null;
  projected_arrive_local?: string | null;
  late_risk?: boolean;
  delivery?: { ok?: boolean; queued?: boolean; error?: string; via?: string };
  force?: boolean;
  reason?: string;
};

type AlertsResp = { alerts: AlertEntry[] };

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtAge = (isoStr: string): string => {
  try {
    const dt = new Date(isoStr);
    const diffS = (Date.now() - dt.getTime()) / 1000;
    if (diffS < 60) return `${Math.round(diffS)}s ago`;
    if (diffS < 3600) return `${Math.round(diffS / 60)}m ago`;
    return `${Math.round(diffS / 3600)}h ago`;
  } catch {
    return isoStr;
  }
};

const statusColor = (s: string): string => {
  if (s === "ok") return "#16a34a";
  if (s === "needs_key" || s === "missing_deps") return "#f59e0b";
  return "#ef4444";
};

const legStatusIcon: Record<string, string> = {
  past: "✓",
  current: "▶",
  before: "·",
};

// ── Tab components ────────────────────────────────────────────────────────────

function StatusTab({ brief, onPollNow, polling }: {
  brief: Brief | null;
  onPollNow: () => void;
  polling: boolean;
}) {
  if (!brief) return <p>Loading status…</p>;

  if (!brief.active) {
    return (
      <div>
        <p className="muted">No active schedule right now.</p>
        <button onClick={onPollNow} disabled={polling}>{polling ? "Polling…" : "poll now"}</button>
      </div>
    );
  }

  const lateRisk = brief.late_risk;
  const rtAvail = brief.realtime_status?.available;
  const rtReason = brief.realtime_status?.reason;

  return (
    <div>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "0.5rem", marginBottom: "1rem" }}>
        <div>
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem", color: "var(--muted)" }}>
            {brief.schedule_id} · {brief.direction === "to-work" ? "→ work" : "← home"}
          </span>
          {lateRisk && (
            <span style={{ marginLeft: "0.75rem", background: "#ef4444", color: "#fff", borderRadius: "4px", padding: "2px 8px", fontSize: "0.78rem", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
              <AlertTriangle size={12} aria-hidden /> late risk +{Math.round(brief.late_by_min ?? 0)}m
            </span>
          )}
        </div>
        <button onClick={onPollNow} disabled={polling} style={{ fontSize: "0.85rem" }}>
          {polling ? "polling…" : "poll now"}
        </button>
      </div>

      {/* Realtime data banner */}
      {!rtAvail && (
        <p style={{ fontSize: "0.82rem", color: "#f59e0b", marginBottom: "0.75rem" }}>
          {rtReason === "needs_511_key"
            ? "No realtime data — set WORKSHOP_511_API_KEY (free at 511.org/open-data/token). Times below are static schedule."
            : `Realtime unavailable (${rtReason ?? "unknown"}) — times below are static schedule.`}
        </p>
      )}

      {/* Summary grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "0.5rem", marginBottom: "1.25rem" }}>
        {brief.leave_by_local && (
          <div className="card" style={{ padding: "0.65rem 0.85rem" }}>
            <p className="muted" style={{ margin: 0, fontSize: "0.75rem" }}>leave by</p>
            <p style={{ margin: 0, fontWeight: 700, fontSize: "1.05rem" }}>{brief.leave_by_local}</p>
          </div>
        )}
        {brief.target_arrive_local && (
          <div className="card" style={{ padding: "0.65rem 0.85rem" }}>
            <p className="muted" style={{ margin: 0, fontSize: "0.75rem" }}>target arrive</p>
            <p style={{ margin: 0, fontSize: "1rem" }}>{brief.target_arrive_local}</p>
            {brief.flex_minutes_late != null && (
              <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--muted)" }}>
                +{brief.flex_minutes_late}m flex → {brief.flex_deadline_local}
              </p>
            )}
          </div>
        )}
        {brief.projected_arrive_local && (
          <div className="card" style={{ padding: "0.65rem 0.85rem", borderColor: lateRisk ? "#ef4444" : undefined }}>
            <p className="muted" style={{ margin: 0, fontSize: "0.75rem" }}>projected arrive</p>
            <p style={{ margin: 0, fontWeight: 600, fontSize: "1rem", color: lateRisk ? "#ef4444" : undefined }}>
              {brief.projected_arrive_local}
            </p>
            <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--muted)" }}>
              {rtAvail ? "live" : "static schedule"}
            </p>
          </div>
        )}
      </div>

      {/* Timeline */}
      {brief.timeline && brief.timeline.length > 0 && (
        <section style={{ marginBottom: "1.25rem" }}>
          <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", margin: "0 0 0.5rem" }}>Timeline</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            {brief.timeline.map((leg, i) => {
              const isCurrent = leg.status === "current";
              const icon = legStatusIcon[leg.status ?? "before"] ?? "·";
              const rt = leg.realtime;
              const rtTag = !rt ? "(no data)"
                : rt.available
                  ? (rt.delay_min && rt.delay_min > 0 ? `realtime +${rt.delay_min}m` : "realtime on-time")
                  : rt.reason === "needs_511_key" ? "(static — no 511 key)"
                  : rt.reason === "no_match" ? "(static — no match)"
                  : `(static — ${rt.reason ?? "no data"})`;
              const route = leg.preferred_train ?? leg.preferred_route;
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: "0.6rem",
                    alignItems: "baseline",
                    padding: "0.4rem 0.6rem",
                    borderRadius: "6px",
                    background: isCurrent ? "var(--card)" : "transparent",
                    fontWeight: isCurrent ? 600 : 400,
                    fontSize: "0.88rem",
                    border: isCurrent ? "1px solid var(--accent)" : "1px solid transparent",
                  }}
                >
                  <span style={{ color: isCurrent ? "var(--accent)" : "var(--muted)", flexShrink: 0, width: "1ch" }}>{icon}</span>
                  <span style={{ color: "var(--muted)", flexShrink: 0, fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                    {leg.depart_local} → {leg.arrive_local}
                  </span>
                  <span>
                    {leg.mode}{route && route !== leg.mode ? ` (${route})` : ""}{" "}
                    <span style={{ color: "var(--muted)", fontWeight: 400 }}>{leg.from} → {leg.to}</span>
                  </span>
                  <span style={{ marginLeft: "auto", color: rt?.available ? "#16a34a" : "var(--muted)", fontSize: "0.78rem", flexShrink: 0 }}>{rtTag}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Service alerts */}
      {brief.alerts && brief.alerts.length > 0 && (
        <section>
          <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", margin: "0 0 0.5rem" }}>
            Service alerts ({brief.alerts.length})
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            {brief.alerts.map((a, i) => (
              <div key={i} className="card" style={{ padding: "0.5rem 0.75rem", borderLeft: "3px solid #f59e0b" }}>
                <span style={{ fontSize: "0.75rem", color: "var(--muted)", marginRight: "0.5rem" }}>[{a.source}]</span>
                <span style={{ fontSize: "0.88rem" }}>{a.header}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ── ScheduleTab — proper form UI (no more JSON textarea) ─────────────────

type DayKey = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
const DAYS: { key: DayKey; label: string }[] = [
  { key: "mon", label: "M" },
  { key: "tue", label: "T" },
  { key: "wed", label: "W" },
  { key: "thu", label: "T" },
  { key: "fri", label: "F" },
  { key: "sat", label: "S" },
  { key: "sun", label: "S" },
];

type ScheduleEntry = {
  id: string;
  direction: "to-work" | "to-home";
  days: string[];
  target_depart_home?: string;
  target_arrive_work?: string;
  target_depart_work?: string;
  target_arrive_home?: string;
  flex_minutes_late?: number;
  flex_minutes_early?: number | null;
  legs_id?: string;
};

type ScheduleDoc = {
  operator?: string;
  home?: { address?: string; lat?: number | null; lon?: number | null };
  work?: { address?: string; lat?: number | null; lon?: number | null };
  schedules: ScheduleEntry[];
  notes?: string;
};

function ScheduleTab() {
  const [doc, setDoc] = useState<ScheduleDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/commute/schedule");
      if (!r.ok) throw new Error(`${r.status}`);
      setDoc(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!doc) return;
    setSaving(true); setErr(null); setSaved(false);
    try {
      const r = await fetch("/api/commute/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: doc }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const updateAddress = (which: "home" | "work", address: string) => {
    if (!doc) return;
    setDoc({ ...doc, [which]: { ...(doc[which] || {}), address } });
  };

  const updateSchedule = (idx: number, patch: Partial<ScheduleEntry>) => {
    if (!doc) return;
    const next = [...doc.schedules];
    next[idx] = { ...next[idx], ...patch };
    setDoc({ ...doc, schedules: next });
  };

  const toggleDay = (idx: number, day: DayKey) => {
    if (!doc) return;
    const cur = new Set(doc.schedules[idx].days || []);
    if (cur.has(day)) cur.delete(day); else cur.add(day);
    updateSchedule(idx, { days: Array.from(cur) });
  };

  const addSchedule = (direction: "to-work" | "to-home") => {
    if (!doc) return;
    const newEntry: ScheduleEntry = {
      id: `${direction}-${Date.now()}`,
      direction,
      days: ["mon", "tue", "wed", "thu", "fri"],
      flex_minutes_late: 15,
      legs_id: direction === "to-work" ? "to-work-default" : "to-home-default",
      ...(direction === "to-work"
        ? { target_depart_home: "07:30", target_arrive_work: "09:00" }
        : { target_depart_work: "18:00", target_arrive_home: "19:45" }),
    };
    setDoc({ ...doc, schedules: [...doc.schedules, newEntry] });
  };

  const removeSchedule = (idx: number) => {
    if (!doc) return;
    if (!confirm(`Remove schedule "${doc.schedules[idx].id}"?`)) return;
    setDoc({ ...doc, schedules: doc.schedules.filter((_, i) => i !== idx) });
  };

  if (loading) return <p>Loading schedule…</p>;
  if (!doc) return <p className="err">{err || "no schedule loaded"}</p>;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", gap: "0.5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: "1rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Schedule</h3>
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          {saved && <span style={{ color: "#16a34a", fontSize: "0.85rem" }}>saved ✓</span>}
          {err && <span style={{ color: "var(--err)", fontSize: "0.82rem" }}>{err}</span>}
          <button onClick={save} disabled={saving}>{saving ? "saving…" : "save"}</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h4 style={{ margin: "0 0 0.6rem", fontSize: "0.85rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Endpoints</h4>
        <div style={{ display: "grid", gridTemplateColumns: "min(110px, 30%) 1fr", gap: "0.5rem 0.75rem", alignItems: "center" }}>
          <label htmlFor="home-addr">home</label>
          <input
            id="home-addr"
            value={doc.home?.address || ""}
            onChange={(e) => updateAddress("home", e.target.value)}
            placeholder="street, city, state zip"
            style={inputStyle}
          />
          <label htmlFor="work-addr">work</label>
          <input
            id="work-addr"
            value={doc.work?.address || ""}
            onChange={(e) => updateAddress("work", e.target.value)}
            placeholder="street, city, state zip"
            style={inputStyle}
          />
        </div>
        <p className="muted" style={{ fontSize: "0.78rem", marginTop: "0.5rem" }}>
          coords for location-aware "already at destination" detection live in Workshop → Settings → Commute
        </p>
      </div>

      {doc.schedules.map((s, i) => (
        <div key={`${s.id}-${i}`} className="card" style={{ marginBottom: "0.85rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem", gap: "0.5rem", flexWrap: "wrap" }}>
            <strong style={{ fontSize: "0.95rem" }}>
              {s.direction === "to-work" ? "→ Work" : "→ Home"}
            </strong>
            <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
              <input
                value={s.id}
                onChange={(e) => updateSchedule(i, { id: e.target.value })}
                style={{ ...inputStyle, fontSize: "0.78rem", fontFamily: "ui-monospace, monospace", width: "auto", padding: "0.25rem 0.4rem" }}
                title="schedule id"
              />
              <button onClick={() => removeSchedule(i)} style={{ fontSize: "0.78rem", padding: "0.25rem 0.55rem", color: "var(--err)" }}>
                remove
              </button>
            </div>
          </div>

          <div style={{ marginBottom: "0.6rem" }}>
            <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginBottom: "0.3rem" }}>days</div>
            <div style={{ display: "flex", gap: "0.25rem" }}>
              {DAYS.map((d) => {
                const on = (s.days || []).includes(d.key);
                return (
                  <button
                    key={d.key}
                    type="button"
                    onClick={() => toggleDay(i, d.key)}
                    style={{
                      width: "34px",
                      height: "34px",
                      borderRadius: "50%",
                      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                      background: on ? "var(--accent)" : "transparent",
                      color: on ? "white" : "var(--muted)",
                      fontWeight: on ? 600 : 400,
                      fontSize: "0.85rem",
                      cursor: "pointer",
                    }}
                    title={d.key}
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "min(140px, 40%) 1fr", gap: "0.4rem 0.75rem", alignItems: "center", marginBottom: "0.4rem" }}>
            {s.direction === "to-work" ? (
              <>
                <label>depart home</label>
                <input
                  type="time"
                  value={s.target_depart_home || ""}
                  onChange={(e) => updateSchedule(i, { target_depart_home: e.target.value })}
                  style={inputStyle}
                />
                <label>arrive work</label>
                <input
                  type="time"
                  value={s.target_arrive_work || ""}
                  onChange={(e) => updateSchedule(i, { target_arrive_work: e.target.value })}
                  style={inputStyle}
                />
              </>
            ) : (
              <>
                <label>depart work</label>
                <input
                  type="time"
                  value={s.target_depart_work || ""}
                  onChange={(e) => updateSchedule(i, { target_depart_work: e.target.value })}
                  style={inputStyle}
                />
                <label>arrive home</label>
                <input
                  type="time"
                  value={s.target_arrive_home || ""}
                  onChange={(e) => updateSchedule(i, { target_arrive_home: e.target.value })}
                  style={inputStyle}
                />
              </>
            )}
            <label>flex (late)</label>
            <input
              type="number"
              min={0}
              max={120}
              value={s.flex_minutes_late ?? 15}
              onChange={(e) => updateSchedule(i, { flex_minutes_late: Number(e.target.value) })}
              style={inputStyle}
            />
            <label>legs preset</label>
            <input
              value={s.legs_id || ""}
              onChange={(e) => updateSchedule(i, { legs_id: e.target.value })}
              placeholder="to-work-default"
              style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
            />
          </div>
        </div>
      ))}

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
        <button onClick={() => addSchedule("to-work")}>+ AM (to-work)</button>
        <button onClick={() => addSchedule("to-home")}>+ PM (to-home)</button>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "0.4rem 0.55rem",
  background: "var(--card)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  fontFamily: "inherit",
  fontSize: "0.9rem",
  boxSizing: "border-box",
};

function SourcesTab() {
  const [data, setData] = useState<SourcesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/commute/sources");
      if (!r.ok) throw new Error(`${r.status}`);
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!data) return <p>Loading sources…</p>;

  const registry = data.registry ?? {};
  const status = data.status ?? {};
  const keys = Array.from(new Set([...Object.keys(registry), ...Object.keys(status)])).sort();

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{keys.length} source{keys.length === 1 ? "" : "s"}</p>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>
      {keys.length === 0 ? (
        <p className="muted">No sources configured.</p>
      ) : (
        <ul className="cards">
          {keys.map((key) => {
            const st = status[key];
            const color = st ? statusColor(st.status) : "var(--muted)";
            const entityCount = st?.entity_count;
            return (
              <li key={key} className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                  <h3 style={{ margin: 0, fontSize: "0.95rem", fontFamily: "ui-monospace, monospace" }}>{key}</h3>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem", color, fontSize: "0.82rem", fontWeight: 600 }}>
                    <Circle size={9} fill="currentColor" strokeWidth={0} aria-hidden />
                    {st?.status ?? "no data"}
                  </span>
                </div>
                <p className="muted" style={{ margin: 0, fontSize: "0.8rem" }}>
                  {st?.ts ? `last poll ${fmtAge(st.ts)}` : "never polled"}
                  {entityCount != null ? ` · ${entityCount} entities` : ""}
                </p>
                {st?.note && (
                  <p style={{ margin: "0.25rem 0 0", fontSize: "0.78rem", color: "#f59e0b" }}>{st.note}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function AlertsTab() {
  const [data, setData] = useState<AlertsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/commute/alerts/recent?limit=50");
      if (!r.ok) throw new Error(`${r.status}`);
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!data) return <p>Loading alerts…</p>;

  const alerts = [...(data.alerts ?? [])].reverse();

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{alerts.length} dispatch{alerts.length === 1 ? "" : "es"}</p>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>
      {alerts.length === 0 ? (
        <p className="muted">No alerts dispatched yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th>Time</th>
                <th>Kind</th>
                <th>Schedule</th>
                <th>Late by</th>
                <th>Delivery</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a, i) => {
                const ok = a.delivery?.ok || a.delivery?.queued;
                const deliveryColor = ok ? "#16a34a" : "#ef4444";
                const deliveryLabel = a.delivery?.ok ? "sent"
                  : a.delivery?.queued ? `queued (${a.delivery.via ?? "file"})`
                  : a.delivery?.error ? `err: ${a.delivery.error.slice(0, 40)}`
                  : "—";
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", whiteSpace: "nowrap" }}>{a.ts}</td>
                    <td style={{ fontSize: "0.85rem" }}>
                      {a.kind ?? (a.late_risk ? "late-risk" : "brief")}
                      {a.checkpoint ? ` · ${a.checkpoint}` : ""}
                      {a.force ? " (forced)" : ""}
                    </td>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>{a.schedule_id ?? "—"}</td>
                    <td style={{ fontSize: "0.85rem", color: a.late_by_min ? "#ef4444" : "var(--muted)" }}>
                      {a.late_by_min != null ? `+${Math.round(a.late_by_min)}m` : "—"}
                    </td>
                    <td>
                      <span style={{ color: deliveryColor, fontSize: "0.82rem", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                        <Circle size={9} fill="currentColor" strokeWidth={0} aria-hidden />
                        {deliveryLabel}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = "status" | "schedule" | "sources" | "alerts";

const TABS: { key: Tab; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "schedule", label: "Schedule" },
  { key: "sources", label: "Sources" },
  { key: "alerts", label: "Alerts" },
];

export default function Commute() {
  const [tab, setTab] = useState<Tab>("status");
  const [brief, setBrief] = useState<Brief | null>(null);
  const [briefErr, setBriefErr] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadBrief = useCallback(async () => {
    try {
      const r = await fetch("/api/commute/status");
      if (!r.ok) throw new Error(`${r.status}`);
      setBrief(await r.json());
      setBriefErr(null);
    } catch (e) {
      setBriefErr(String(e));
    }
  }, []);

  const pollNow = useCallback(async () => {
    setPolling(true);
    try {
      await fetch("/api/commute/poll", { method: "POST" });
      await loadBrief();
    } finally {
      setPolling(false);
    }
  }, [loadBrief]);

  useEffect(() => {
    loadBrief();
    refreshTimer.current = setInterval(loadBrief, 30_000);
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, [loadBrief]);

  const select = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  return (
    <div>
      {briefErr && tab === "status" && (
        <p className="err" style={{ marginBottom: "0.75rem" }}>Status error: {briefErr}</p>
      )}

      {/* Tab bar */}
      <div style={{ display: "flex", gap: "0.25rem", marginBottom: "1rem", borderBottom: "1px solid var(--border)" }}>
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => select(t.key)}
              style={{
                background: active ? "var(--card)" : "transparent",
                color: active ? "var(--accent)" : "var(--muted)",
                fontWeight: active ? 600 : 400,
                border: "none",
                padding: "0.5rem 0.85rem",
                borderRadius: "6px 6px 0 0",
                cursor: "pointer",
                fontSize: "0.95rem",
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "status" && <StatusTab brief={brief} onPollNow={pollNow} polling={polling} />}
      {tab === "schedule" && <ScheduleTab />}
      {tab === "sources" && <SourcesTab />}
      {tab === "alerts" && <AlertsTab />}
    </div>
  );
}
