import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Circle, RefreshCw, CalendarClock, Check, Play, Dot,
  ChevronDown, ChevronRight, Inbox, Sparkles } from "lucide-react";
import { Stepper, Tabs } from "../../../ui";

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
  next_schedule?: {
    schedule_id?: string;
    direction?: string;
    day?: string;
    date_local?: string;
    leave_by_local?: string;
    target_arrive_local?: string;
  } | null;
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
  if (s === "ok") return "var(--ok)";
  if (s === "needs_key" || s === "missing_deps") return "var(--warn)";
  return "var(--danger)";
};

// Leg status → lucide icon (no emoji — operator rule).
const legStatusIcon = {
  past: Check,
  current: Play,
  before: Dot,
} as const;

// ── Tab components ────────────────────────────────────────────────────────────

// Re-fetch live transit data on demand. Labeled + subtitled so it's obvious
// what it does (the old bare "poll now" wasn't).
function RefreshButton({ onPollNow, polling, compact }: {
  onPollNow: () => void;
  polling: boolean;
  compact?: boolean;
}) {
  const btn = (
    <button
      onClick={onPollNow}
      disabled={polling}
      title="Re-fetch live Caltrain & Muni arrival data right now"
      style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}
    >
      <RefreshCw size={13} strokeWidth={2} aria-hidden />
      {polling ? "Refreshing…" : "Refresh transit data"}
    </button>
  );
  if (compact) return btn;
  return (
    <div style={{ display: "inline-flex", flexDirection: "column", gap: "0.2rem" }}>
      {btn}
      <span className="muted" style={{ fontSize: "0.72rem" }}>Re-fetches live Caltrain &amp; Muni arrivals now</span>
    </div>
  );
}

function StatusTab({ brief, onPollNow, polling }: {
  brief: Brief | null;
  onPollNow: () => void;
  polling: boolean;
}) {
  if (!brief) return <p>Loading status…</p>;

  if (!brief.active) {
    const nx = brief.next_schedule;
    return (
      <div>
        {nx ? (
          <div className="card" style={{ padding: "1rem 1.1rem", marginBottom: "1rem" }}>
            <p className="muted" style={{ margin: "0 0 0.4rem", display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.74rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              <CalendarClock size={14} aria-hidden /> Next commute
            </p>
            <p style={{ margin: 0, fontSize: "1.05rem", fontWeight: 600 }}>
              {nx.date_local} · {nx.direction === "to-work" ? "→ Work" : "→ Home"}
            </p>
            <div style={{ display: "flex", gap: "1.5rem", marginTop: "0.55rem" }}>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.72rem" }}>leave by</p>
                <p style={{ margin: 0, fontWeight: 700 }}>{nx.leave_by_local}</p>
              </div>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.72rem" }}>arrive by</p>
                <p style={{ margin: 0 }}>{nx.target_arrive_local}</p>
              </div>
            </div>
          </div>
        ) : (
          <p className="muted" style={{ marginBottom: "1rem" }}>No active schedule right now, and nothing upcoming on the calendar.</p>
        )}
        <RefreshButton onPollNow={onPollNow} polling={polling} />
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
            <span style={{ marginLeft: "0.75rem", background: "var(--danger-soft)", color: "var(--danger)", borderRadius: "4px", padding: "2px 8px", fontSize: "0.78rem", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
              <AlertTriangle size={12} aria-hidden /> late risk +{Math.round(brief.late_by_min ?? 0)}m
            </span>
          )}
        </div>
        <RefreshButton onPollNow={onPollNow} polling={polling} compact />
      </div>

      {/* Realtime data banner */}
      {!rtAvail && (
        <p style={{ fontSize: "0.82rem", color: "var(--warn)", marginBottom: "0.75rem" }}>
          {rtReason === "needs_511_key"
            ? "No realtime data. Add a 511 key in Commute settings. Times below are the static schedule."
            : `Realtime unavailable (${rtReason ?? "unknown"}). Times below are the static schedule.`}
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
          <div className="card" style={{ padding: "0.65rem 0.85rem", borderColor: lateRisk ? "var(--danger)" : undefined }}>
            <p className="muted" style={{ margin: 0, fontSize: "0.75rem" }}>projected arrive</p>
            <p style={{ margin: 0, fontWeight: 600, fontSize: "1rem", color: lateRisk ? "var(--danger)" : undefined }}>
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
              const LegIcon = legStatusIcon[(leg.status ?? "before") as keyof typeof legStatusIcon] ?? Dot;
              const rt = leg.realtime;
              const rtTag = !rt ? "(no data)"
                : rt.available
                  ? (rt.delay_min && rt.delay_min > 0 ? `realtime +${rt.delay_min}m` : "realtime on-time")
                  : rt.reason === "needs_511_key" ? "(static, no 511 key)"
                  : rt.reason === "no_match" ? "(static, no match)"
                  : `(static, ${rt.reason ?? "no data"})`;
              const route = leg.preferred_train ?? leg.preferred_route;
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "0.3rem 0.6rem",
                    alignItems: "baseline",
                    padding: "0.4rem 0.6rem",
                    borderRadius: "6px",
                    background: isCurrent ? "var(--card)" : "transparent",
                    fontWeight: isCurrent ? 600 : 400,
                    fontSize: "0.88rem",
                    border: isCurrent ? "1px solid var(--accent)" : "1px solid transparent",
                  }}
                >
                  <span style={{ color: isCurrent ? "var(--accent)" : "var(--muted)", flexShrink: 0, display: "inline-flex", alignItems: "center" }}>
                    <LegIcon size={14} strokeWidth={2.2} fill={isCurrent ? "currentColor" : "none"} aria-hidden />
                  </span>
                  <span style={{ color: "var(--muted)", flexShrink: 0, fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                    {leg.depart_local} → {leg.arrive_local}
                  </span>
                  {/* Grows to fill the row; wraps to its own line on a phone instead of
                      pushing the realtime tag off-screen. */}
                  <span style={{ flex: "1 1 10rem", minWidth: 0 }}>
                    {leg.mode}{route && route !== leg.mode ? ` (${route})` : ""}{" "}
                    <span style={{ color: "var(--muted)", fontWeight: 400 }}>{leg.from} → {leg.to}</span>
                  </span>
                  <span style={{ marginLeft: "auto", color: rt?.available ? "var(--ok)" : "var(--muted)", fontSize: "0.78rem", flexShrink: 0 }}>{rtTag}</span>
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
              <div key={i} className="card" style={{ padding: "0.5rem 0.75rem", borderLeft: "3px solid var(--warn)" }}>
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

type AnchorType = "arrive" | "depart";

type ScheduleEntry = {
  id: string;
  direction: "to-work" | "to-home";
  days: string[];
  // Anchor model: set the ONE end you care about; the other is derived from
  // leg durations by the backend (see scoring.resolve_times).
  anchor?: AnchorType;
  anchor_time?: string;
  buffer_minutes?: number;
  // Legacy two-field form — still read for display of pre-anchor schedules.
  target_depart_home?: string;
  target_arrive_work?: string;
  target_depart_work?: string;
  target_arrive_home?: string;
  flex_minutes_late?: number;
  flex_minutes_early?: number | null;
  legs_id?: string;
};

// Resolve an entry's anchor with a sensible default + legacy fallback so old
// saved schedules still populate the editor.
const entryAnchorType = (s: ScheduleEntry): AnchorType =>
  s.anchor ?? (s.direction === "to-work" ? "arrive" : "depart");
const entryAnchorTime = (s: ScheduleEntry): string => {
  if (s.anchor_time != null) return s.anchor_time;
  return entryAnchorType(s) === "arrive"
    ? s.target_arrive_work ?? s.target_arrive_home ?? ""
    : s.target_depart_home ?? s.target_depart_work ?? "";
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
        ? { anchor: "arrive", anchor_time: "09:00", buffer_minutes: 5 }
        : { anchor: "depart", anchor_time: "18:00" }),
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
          {saved && (
            <span style={{ color: "var(--ok)", fontSize: "0.85rem", display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
              <Check size={13} strokeWidth={2.4} aria-hidden /> saved
            </span>
          )}
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

      {doc.schedules.map((s, i) => {
        const anchorType = entryAnchorType(s);
        const anchorTime = entryAnchorTime(s);
        return (
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
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: "40px",
                      height: "40px",
                      padding: 0,
                      lineHeight: 1,
                      boxSizing: "border-box",
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
            <label htmlFor={`anchor-${i}`}>I care about</label>
            <select
              id={`anchor-${i}`}
              value={anchorType}
              onChange={(e) =>
                updateSchedule(i, {
                  anchor: e.target.value as AnchorType,
                  target_depart_home: undefined, target_arrive_work: undefined,
                  target_depart_work: undefined, target_arrive_home: undefined,
                })
              }
              style={inputStyle}
            >
              <option value="arrive">arrive by</option>
              <option value="depart">depart at</option>
            </select>
            <label>{anchorType === "arrive" ? "arrive by" : "depart at"}</label>
            <input
              type="time"
              value={anchorTime}
              onChange={(e) =>
                updateSchedule(i, {
                  anchor: anchorType, anchor_time: e.target.value,
                  target_depart_home: undefined, target_arrive_work: undefined,
                  target_depart_work: undefined, target_arrive_home: undefined,
                })
              }
              style={inputStyle}
            />
            {anchorType === "arrive" && (
              <>
                <label>buffer (min early)</label>
                <Stepper
                  value={s.buffer_minutes ?? 0}
                  min={0}
                  max={60}
                  suffix="min"
                  onChange={(v) => updateSchedule(i, { buffer_minutes: v })}
                />
              </>
            )}
            <label>flex (late)</label>
            <Stepper
              value={s.flex_minutes_late ?? 15}
              min={0}
              max={120}
              suffix="min"
              onChange={(v) => updateSchedule(i, { flex_minutes_late: v })}
            />
            <label>legs preset</label>
            <input
              value={s.legs_id || ""}
              onChange={(e) => updateSchedule(i, { legs_id: e.target.value })}
              placeholder="to-work-default"
              style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
            />
          </div>
          <p className="muted" style={{ fontSize: "0.76rem", margin: "0.1rem 0 0" }}>
            {anchorType === "arrive"
              ? "Leave-by is computed from leg durations (minus buffer), shown live on the Status tab."
              : "Arrival is computed from leg durations, shown live on the Status tab."}
          </p>
        </div>
        );
      })}

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
  // The registry object carries the feed list under `sources` plus non-source
  // metadata siblings (e.g. `shared_rate_limit_pool`). Only the entries in
  // `sources` are real pollable feeds — render cards from those, not from every
  // top-level key (which used to surface phantom "no data" rows).
  type RegSource = { key?: string; name?: string; status?: string; fallback?: string; notes?: string };
  const regSources = Array.isArray((registry as { sources?: unknown }).sources)
    ? ((registry as { sources: RegSource[] }).sources)
    : [];
  type SourceRow = { key: string; name?: string; regStatus?: string; note?: string };
  const byKey = new Map<string, SourceRow>();
  for (const s of regSources) {
    if (s && typeof s.key === "string") {
      byKey.set(s.key, { key: s.key, name: s.name, regStatus: s.status, note: s.fallback ?? s.notes });
    }
  }
  // Defensive: surface any live status whose key isn't in the registry.
  for (const k of Object.keys(status)) if (!byKey.has(k)) byKey.set(k, { key: k });
  const sources = Array.from(byKey.values()).sort((a, b) => a.key.localeCompare(b.key));

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{sources.length} source{sources.length === 1 ? "" : "s"}</p>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>
      {sources.length === 0 ? (
        <p className="muted">No sources configured.</p>
      ) : (
        <ul className="cards">
          {sources.map((src) => {
            const key = src.key;
            const st = status[key];
            const color = st ? statusColor(st.status) : "var(--muted)";
            const entityCount = st?.entity_count;
            return (
              <li key={key} className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                  <h3 style={{ margin: 0, fontSize: "0.95rem", fontFamily: "ui-monospace, monospace" }} title={src.name ?? key}>{key}</h3>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem", color, fontSize: "0.82rem", fontWeight: 600 }}>
                    <Circle size={9} fill="currentColor" strokeWidth={0} aria-hidden />
                    {st?.status ?? src.regStatus ?? "not configured"}
                  </span>
                </div>
                <p className="muted" style={{ margin: 0, fontSize: "0.8rem" }}>
                  {st?.ts
                    ? `last poll ${fmtAge(st.ts)}`
                    : "never polled (no live poller for this source yet)"}
                  {entityCount != null ? ` · ${entityCount} entities` : ""}
                </p>
                {(st?.note || (!st && src.note)) && (
                  <p style={{ margin: "0.25rem 0 0", fontSize: "0.78rem", color: "var(--warn)" }}>{st?.note ?? src.note}</p>
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
                const deliveryColor = ok ? "var(--ok)" : "var(--danger)";
                const deliveryLabel = a.delivery?.ok ? "sent"
                  : a.delivery?.queued ? `queued (${a.delivery.via ?? "file"})`
                  : a.delivery?.error ? `err: ${a.delivery.error.slice(0, 40)}`
                  : "–";
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", whiteSpace: "nowrap" }}>{new Date(a.ts).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
                    <td style={{ fontSize: "0.85rem" }}>
                      {a.kind ?? (a.late_risk ? "late-risk" : "brief")}
                      {a.checkpoint ? ` · ${a.checkpoint}` : ""}
                      {a.force ? " (forced)" : ""}
                    </td>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>{a.schedule_id ?? "–"}</td>
                    <td style={{ fontSize: "0.85rem", color: a.late_by_min ? "var(--danger)" : "var(--muted)" }}>
                      {a.late_by_min != null ? `+${Math.round(a.late_by_min)}m` : "–"}
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

// ── Now feed (adaptive messages) ───────────────────────────────────────────────

type Msg = {
  id: string;
  ts: string;
  kind: string;
  severity: "ok" | "info" | "caution" | "urgent";
  headline: string;
  detail?: string;
  reasoning?: string;
  confidence?: "high" | "medium" | "low";
  action?: string | null;
  evidence?: Record<string, unknown>;
  source?: string;
};
type MsgResp = { messages: Msg[]; total: number; has_more: boolean };

const SEV_COLOR: Record<string, string> = {
  ok: "var(--ok)",
  info: "var(--accent, var(--fg))",
  caution: "var(--warn)",
  urgent: "var(--danger)",
};

function sevSoft(sev: string): string {
  const c = SEV_COLOR[sev] ?? "var(--muted)";
  return `color-mix(in srgb, ${c} 14%, transparent)`;
}

function SevPill({ sev }: { sev: string }) {
  const c = SEV_COLOR[sev] ?? "var(--muted)";
  return (
    <span style={{
      color: c, background: sevSoft(sev), border: `1px solid color-mix(in srgb, ${c} 40%, transparent)`,
      borderRadius: 999, padding: "0.1rem 0.5rem", fontSize: "0.72rem", fontWeight: 600,
      textTransform: "uppercase", letterSpacing: "0.03em", whiteSpace: "nowrap",
    }}>{sev}</span>
  );
}

function Evidence({ msg }: { msg: Msg }) {
  const ev = msg.evidence ?? {};
  const keys = Object.keys(ev);
  return (
    <div style={{ marginTop: "0.5rem", paddingTop: "0.5rem", borderTop: "1px solid var(--border)" }}>
      <p className="muted" style={{ margin: "0 0 0.3rem", fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.04em" }}>Evidence</p>
      {keys.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: "0.8rem" }}>No structured evidence recorded.</p>
      ) : (
        <pre style={{ margin: 0, fontSize: "0.74rem", whiteSpace: "pre-wrap", wordBreak: "break-word",
          fontFamily: "ui-monospace, monospace", color: "var(--muted)", maxHeight: 260, overflow: "auto" }}>
          {JSON.stringify(ev, null, 2)}
        </pre>
      )}
      <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.7rem" }}>
        {msg.source ? `via ${msg.source}` : ""}{msg.confidence ? ` · ${msg.confidence} confidence` : ""}
      </p>
    </div>
  );
}

function TopMessageCard({ msg }: { msg: Msg }) {
  const [open, setOpen] = useState(false);
  const c = SEV_COLOR[msg.severity] ?? "var(--muted)";
  return (
    <div style={{ border: `1px solid color-mix(in srgb, ${c} 45%, var(--border))`, borderRadius: 12,
      background: sevSoft(msg.severity), padding: "1rem", marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.4rem", flexWrap: "wrap" }}>
        <SevPill sev={msg.severity} />
        <span className="muted" style={{ fontSize: "0.75rem" }}>{fmtAge(msg.ts)}</span>
        <span className="muted" style={{ fontSize: "0.72rem", marginLeft: "auto" }}>{msg.kind}</span>
      </div>
      <h3 style={{ margin: "0 0 0.3rem", fontSize: "1.12rem", lineHeight: 1.25 }}>{msg.headline}</h3>
      {msg.detail && <p style={{ margin: "0 0 0.35rem", fontSize: "0.92rem" }}>{msg.detail}</p>}
      {msg.reasoning && <p className="muted" style={{ margin: "0 0 0.35rem", fontSize: "0.85rem", fontStyle: "italic" }}>{msg.reasoning}</p>}
      {msg.action && (
        <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", fontWeight: 600, color: c, display: "flex", alignItems: "center", gap: "0.3rem" }}>
          <ChevronRight size={15} /> {msg.action}
        </p>
      )}
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
        style={{ marginTop: "0.5rem", background: "none", border: "none", cursor: "pointer", color: "var(--muted)",
          fontSize: "0.78rem", display: "inline-flex", alignItems: "center", gap: "0.25rem", padding: 0 }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Why
      </button>
      {open && <Evidence msg={msg} />}
    </div>
  );
}

function HistoryRow({ msg }: { msg: Msg }) {
  const [open, setOpen] = useState(false);
  const c = SEV_COLOR[msg.severity] ?? "var(--muted)";
  return (
    <li style={{ borderBottom: "1px solid var(--border)", padding: "0.55rem 0" }}>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
        style={{ width: "100%", background: "none", border: "none", cursor: "pointer", color: "inherit",
          display: "flex", alignItems: "center", gap: "0.55rem", textAlign: "left", padding: 0 }}>
        <Dot size={18} style={{ color: c, flexShrink: 0 }} aria-hidden />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "0.9rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{msg.headline}</span>
          <span className="muted" style={{ fontSize: "0.72rem" }}>{fmtAge(msg.ts)} · {msg.kind}</span>
        </span>
        {open ? <ChevronDown size={15} className="muted" /> : <ChevronRight size={15} className="muted" />}
      </button>
      {open && (
        <div style={{ paddingLeft: "1.1rem", marginTop: "0.4rem" }}>
          {msg.detail && <p style={{ margin: "0 0 0.3rem", fontSize: "0.85rem" }}>{msg.detail}</p>}
          {msg.reasoning && <p className="muted" style={{ margin: 0, fontSize: "0.82rem", fontStyle: "italic" }}>{msg.reasoning}</p>}
          <Evidence msg={msg} />
        </div>
      )}
    </li>
  );
}

const PAGE = 12;

function NowTab() {
  const [items, setItems] = useState<Msg[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);

  const loadFirst = useCallback(async () => {
    try {
      const r = await fetch(`/api/commute/messages?limit=${PAGE}&offset=0`);
      if (!r.ok) throw new Error(`${r.status}`);
      const d: MsgResp = await r.json();
      setItems(d.messages);
      setTotal(d.total);
      setHasMore(d.has_more);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const loadMore = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/commute/messages?limit=${PAGE}&offset=${items.length}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const d: MsgResp = await r.json();
      setItems((prev) => [...prev, ...d.messages]);
      setHasMore(d.has_more);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [items.length]);

  // "Check now" composes a fresh adaptive message server-side and reloads.
  const checkNow = useCallback(async () => {
    setChecking(true);
    try {
      await fetch("/api/commute/reason", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkpoint: "during", dispatch: "no", force_llm: true }),
      });
      await loadFirst();
    } finally {
      setChecking(false);
    }
  }, [loadFirst]);

  useEffect(() => {
    loadFirst();
    const t = setInterval(loadFirst, 30_000);
    return () => clearInterval(t);
  }, [loadFirst]);

  if (err && items.length === 0) {
    return <p className="err">Couldn’t load messages: {err} <code>/api/commute/messages</code></p>;
  }

  const top = items[0];
  const rest = items.slice(1);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{total} message{total === 1 ? "" : "s"}</p>
        <button onClick={checkNow} disabled={checking}
          style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}>
          <Sparkles size={14} /> {checking ? "Thinking…" : "Check now"}
        </button>
      </div>

      {items.length === 0 ? (
        <div style={{ textAlign: "center", padding: "2rem 1rem", color: "var(--muted)" }}>
          <Inbox size={28} strokeWidth={1.6} />
          <p style={{ margin: "0.5rem 0 0" }}>No commute messages yet.</p>
          <p style={{ margin: "0.25rem 0 0", fontSize: "0.85rem" }}>
            They arrive automatically around your commute. Tap <strong>Check now</strong> for an on-demand read.
          </p>
        </div>
      ) : (
        <>
          {top && <TopMessageCard msg={top} />}
          {rest.length > 0 && (
            <>
              <p className="muted" style={{ margin: "0 0 0.25rem", fontSize: "0.74rem", textTransform: "uppercase", letterSpacing: "0.04em" }}>Earlier</p>
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {rest.map((m) => <HistoryRow key={m.id} msg={m} />)}
              </ul>
            </>
          )}
          {hasMore && (
            <div style={{ textAlign: "center", marginTop: "0.9rem" }}>
              <button onClick={loadMore} disabled={loading}>{loading ? "…" : "Load more"}</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = "now" | "status" | "schedule" | "sources" | "alerts";

const TABS: { key: Tab; label: string }[] = [
  { key: "now", label: "Now" },
  { key: "status", label: "Status" },
  { key: "schedule", label: "Schedule" },
  { key: "sources", label: "Sources" },
  { key: "alerts", label: "Alerts" },
];

export default function Commute() {
  const [tab, setTab] = useState<Tab>("now");
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

      <div style={{ marginBottom: "var(--space-5)" }}>
        <Tabs items={TABS.map((t) => ({ value: t.key, label: t.label }))} value={tab} onChange={select} />
      </div>

      {tab === "now" && <NowTab />}
      {tab === "status" && <StatusTab brief={brief} onPollNow={pollNow} polling={polling} />}
      {tab === "schedule" && <ScheduleTab />}
      {tab === "sources" && <SourcesTab />}
      {tab === "alerts" && <AlertsTab />}
    </div>
  );
}
