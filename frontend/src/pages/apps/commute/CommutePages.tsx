import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Circle, RefreshCw, CalendarClock, Check, Play, Dot,
  ChevronDown, ChevronRight, Inbox, Sparkles } from "lucide-react";
import { Button, ConfirmDialog, Notice, Skeleton, StateBlock, StatusPill, Stepper, Tabs, type StatusKind, shortHttpError, timeAgo } from "@workshop/ui";
import {
  commuteKeys, commuteScheduleQuery, commuteStatusQuery,
  fetchAlertsRecent, fetchMessages, fetchSources,
  type ScheduleDoc, type ScheduleEntry,
} from "./queries";

// EVERY SERVER READ ON THIS PAGE IS A QUERY, and the keys are built in
// `./queries.ts`. What that replaced was five `usePoll` timers, each with its
// own `useState` copy of a payload, its own `loading` boolean and its own
// swallowed error -- including two polls of `/commute/status` at two cadences
// (one here, one in `useCommuteNative`) and a messages poll that overwrote the
// pages the reader had asked for. See the comments at each call site.

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


/** A caught value as a sentence. Used wherever a failed read is SAID. */
const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The placeholder a read shows while it has nothing yet. Three lines, because
 *  every one of these regions opens with a heading and a couple of rows. */
function LoadingRows() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
      <Skeleton width="40%" />
      <Skeleton />
      <Skeleton width="70%" />
    </div>
  );
}

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

function StatusTab({ brief, loading, error, pollError, onRetry, onPollNow, polling }: {
  brief: Brief | null;
  loading: boolean;
  error: string | null;
  pollError: string | null;
  onRetry: () => void;
  onPollNow: () => void;
  polling: boolean;
}) {
  // A REGION when the brief is not there, a LINE when it is (frontend/CLAUDE.md,
  // "A region or a line"). The old code had neither: a failed read left the
  // last good brief on screen and said nothing at all, and the parent painted a
  // bare red paragraph above the tabs.
  if (!brief) {
    if (loading) return <LoadingRows />;
    return (
      <StateBlock
        variant="error"
        title="Couldn't load the commute brief"
        hint={<>The status read failed. <code>/api/commute/status</code></>}
        action={<Button onClick={onRetry}>Retry</Button>}
      />
    );
  }

  const notice = error ? (
    <Notice tone="error" detail={error} action={<Button size="sm" onClick={onRetry}>Retry</Button>}>
      The brief below stopped updating.
    </Notice>
  ) : pollError ? (
    <Notice tone="error" detail={pollError}>The transit refresh did not go through.</Notice>
  ) : null;

  if (!brief.active) {
    const nx = brief.next_schedule;
    return (
      <div>
        {notice}
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
      {notice}
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

// The doc and its entries are declared once, beside the key, because the app
// settings panel reads the same cache entry. See `queries.ts`.
type AnchorType = NonNullable<ScheduleEntry["anchor"]>;

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

function ScheduleTab() {
  const qc = useQueryClient();
  const q = useQuery(commuteScheduleQuery());

  // THE EDIT BUFFER IS LOCAL STATE, AND IT IS THE ONLY LOCAL STATE LEFT HERE.
  // `draft` is null until the operator changes something; from then on it is
  // what the form renders and what Save posts. There is no effect copying the
  // server doc into state, so the read can refetch (the app settings panel
  // reads this same key, and the app re-reads on focus) without a half-typed
  // address being replaced under the cursor. A form draft is not cache state
  // and must never be written back into the cache.
  const [draft, setDraft] = useState<ScheduleDoc | null>(null);
  const [saved, setSaved] = useState(false);
  const [removingIdx, setRemovingIdx] = useState<number | null>(null);

  const doc = draft ?? q.data ?? null;
  const setDoc = setDraft;

  const save = useMutation({
    mutationFn: async (next: ScheduleDoc) => {
      const r = await fetch("/api/commute/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: next }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
    },
    onSuccess: async () => {
      // Drop the draft and let the server's own copy be what the form shows
      // again, so the editor cannot keep displaying a version the backend
      // normalised. One invalidation reaches every reader of the key.
      setDraft(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      await qc.invalidateQueries({ queryKey: commuteKeys.schedule() });
    },
  });

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

  const removeSchedule = () => {
    if (!doc || removingIdx == null) return;
    setDoc({ ...doc, schedules: doc.schedules.filter((_, i) => i !== removingIdx) });
    setRemovingIdx(null);
  };

  if (q.isPending) return <LoadingRows />;
  if (!doc) {
    // The form is what is missing, so this is a region.
    return (
      <StateBlock
        variant="error"
        title="Couldn't load the schedule"
        hint={<>The weekly plan could not be read. <code>/api/commute/schedule</code></>}
        action={<Button onClick={() => void q.refetch()}>Retry</Button>}
      />
    );
  }

  return (
    <div>
      {/* A rejected save is a LINE: the form it was rejected from is right here. */}
      {save.isError && (
        <Notice
          tone="error"
          detail={errText(save.error)}
          action={<Button size="sm" onClick={() => save.mutate(doc)}>Try again</Button>}
        >
          The schedule was not saved.
        </Notice>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", gap: "0.5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: "1rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Schedule</h3>
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          {saved && (
            <span style={{ color: "var(--ok)", fontSize: "0.85rem", display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
              <Check size={13} strokeWidth={2.4} aria-hidden /> saved
            </span>
          )}
          <Button onClick={() => save.mutate(doc)} disabled={save.isPending}>{save.isPending ? "saving…" : "save"}</Button>
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
              <button onClick={() => setRemovingIdx(i)} style={{ fontSize: "0.78rem", padding: "0.25rem 0.55rem", color: "var(--err)" }}>
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

      <ConfirmDialog
        open={removingIdx != null}
        onCancel={() => setRemovingIdx(null)}
        onConfirm={removeSchedule}
        title="Remove this schedule?"
        body={`Removes "${removingIdx != null ? doc.schedules[removingIdx]?.id ?? "" : ""}" and its departure times from your commute. Once you save, this can't be undone.`}
        confirmLabel="Remove schedule"
      />
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

const SOURCES_POLL_MS = 30_000;

function SourcesTab() {
  // The Refresh button and the 30s cadence are the same read now. They used to
  // be one `useCallback` shared by an onClick and a `usePoll`, which is the
  // same idea with two copies of the in-flight flag.
  const q = useQuery({
    queryKey: commuteKeys.sources(),
    queryFn: () => fetchSources<SourcesResp>(),
    refetchInterval: SOURCES_POLL_MS,
    // Owns its own cadence: a staleness window over it would swallow the
    // catch-up read on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry; the next tick is the retry.
    retry: false,
  });
  const data = q.data ?? null;

  if (!data) {
    if (q.isPending) return <LoadingRows />;
    return (
      <StateBlock
        variant="error"
        title="Couldn't load sources"
        hint={<>The feed registry could not be read. <code>/api/commute/sources</code></>}
        action={<Button onClick={() => void q.refetch()}>Retry</Button>}
      />
    );
  }

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
      {/* A dropped tick beside a list that is still drawn: a line, not a region. */}
      {q.isError && (
        <Notice tone="error" detail={errText(q.error)} action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}>
          Source health is out of date.
        </Notice>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{sources.length} source{sources.length === 1 ? "" : "s"}</p>
        {/* The label does not change on a tick. `isFetching` is true every thirty
            seconds whether or not anyone asked, and a button that renames itself
            on a timer reads as something happening. Dimming while a read is in
            flight is the feedback; a repeat click is coalesced by the query
            layer anyway. */}
        <Button onClick={() => void q.refetch()} disabled={q.isFetching}>Refresh</Button>
      </div>
      {sources.length === 0 ? (
        <StateBlock
          title="No sources configured"
          hint="Transit feeds come from the backend source registry. Add one there and it appears here with its health."
        />
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
                    ? `last poll ${timeAgo(st.ts)}`
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

const ALERTS_POLL_MS = 30_000;
// The tail this view shows. It is in the key as well as in the URL, so a
// 50-row answer can never be served to a caller that asked for fewer.
const ALERTS_LIMIT = 50;

function AlertsTab() {
  const q = useQuery({
    queryKey: commuteKeys.alertsRecent({ limit: ALERTS_LIMIT }),
    queryFn: () => fetchAlertsRecent<AlertsResp>({ limit: ALERTS_LIMIT }),
    refetchInterval: ALERTS_POLL_MS,
    staleTime: 0,
    retry: false,
  });
  const data = q.data ?? null;

  if (!data) {
    if (q.isPending) return <LoadingRows />;
    return (
      <StateBlock
        variant="error"
        title="Couldn't load alerts"
        hint={<>The dispatch log could not be read. <code>/api/commute/alerts/recent</code></>}
        action={<Button onClick={() => void q.refetch()}>Retry</Button>}
      />
    );
  }

  const alerts = [...(data.alerts ?? [])].reverse();

  return (
    <div>
      {q.isError && (
        <Notice tone="error" detail={errText(q.error)} action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}>
          The dispatch log is out of date.
        </Notice>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>
          {alerts.length} dispatch{alerts.length === 1 ? "" : "es"}
          {alerts.length >= ALERTS_LIMIT ? ` (latest ${ALERTS_LIMIT})` : ""}
        </p>
        {/* The label does not change on a tick. `isFetching` is true every thirty
            seconds whether or not anyone asked, and a button that renames itself
            on a timer reads as something happening. Dimming while a read is in
            flight is the feedback; a repeat click is coalesced by the query
            layer anyway. */}
        <Button onClick={() => void q.refetch()} disabled={q.isFetching}>Refresh</Button>
      </div>
      {alerts.length === 0 ? (
        <StateBlock
          title="No alerts dispatched yet"
          hint="An alert is recorded when the commute watcher sends one. Use Check now on the Now tab to run a check immediately."
        />
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

// The now-feed's own severity vocabulary, mapped onto the five design status
// kinds instead of a bespoke three-color scheme (StatusPill, never a hand-
// rolled pill). `info` reads as `stopped`: it is a neutral fact, not an
// outcome, the same way an idle/paused state carries no ok/warn/danger charge.
const SEVERITY_KIND: Record<Msg["severity"], StatusKind> = {
  ok: "ok",
  info: "stopped",
  caution: "warn",
  urgent: "danger",
};

// The kind's own token, for the few places (a card's tinted border, an inline
// action line) that need the bare color rather than a pill. Status tokens are
// named exactly `--ok` / `--stopped` / `--warn` / `--danger`, so this is a
// lookup, not an invented palette.
function sevColor(sev: Msg["severity"]): string {
  return `var(--${SEVERITY_KIND[sev]})`;
}

function sevSoft(sev: Msg["severity"]): string {
  return `color-mix(in srgb, ${sevColor(sev)} 14%, transparent)`;
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
  const c = sevColor(msg.severity);
  return (
    <div style={{ border: `1px solid color-mix(in srgb, ${c} 45%, var(--border))`, borderRadius: 12,
      background: sevSoft(msg.severity), padding: "1rem", marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.4rem", flexWrap: "wrap" }}>
        <StatusPill status={SEVERITY_KIND[msg.severity]}>{msg.severity}</StatusPill>
        <span className="muted" style={{ fontSize: "0.75rem" }}>{timeAgo(msg.ts)}</span>
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
  const c = sevColor(msg.severity);
  return (
    <li style={{ borderBottom: "1px solid var(--border)", padding: "0.55rem 0" }}>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
        style={{ width: "100%", background: "none", border: "none", cursor: "pointer", color: "inherit",
          display: "flex", alignItems: "center", gap: "0.55rem", textAlign: "left", padding: 0 }}>
        <Dot size={18} style={{ color: c, flexShrink: 0 }} aria-hidden />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "0.9rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{msg.headline}</span>
          <span className="muted" style={{ fontSize: "0.72rem" }}>{timeAgo(msg.ts)} · {msg.kind}</span>
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
const MESSAGES_POLL_MS = 30_000;

function NowTab() {
  const qc = useQueryClient();

  // THE PAGED FEED IS ONE QUERY, WHICH IS THE FIX FOR A REAL BUG. It was a
  // `usePoll` over page 1 that called `setItems(page1)` every thirty seconds,
  // beside a "Load more" that appended pages 2..n to the same array: opening
  // three pages and waiting out one tick silently collapsed the list back to
  // twelve rows, with the reader's scroll position stranded below the end of
  // it. The pages the reader has opened are now the query's own state, so the
  // tick refetches ALL of them, in order, into the slots they already occupy.
  const q = useInfiniteQuery({
    queryKey: commuteKeys.messagesPaged({ limit: PAGE }),
    queryFn: ({ pageParam }) => fetchMessages<MsgResp>({ limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    // The next offset is how many rows the SERVER has handed over, not how many
    // are rendered: the render list drops repeats (below) and paging by the
    // deduped count would skip a message.
    getNextPageParam: (last, pages) =>
      last.has_more ? pages.reduce((n, page) => n + page.messages.length, 0) : undefined,
    refetchInterval: MESSAGES_POLL_MS,
    staleTime: 0,
    retry: false,
  });

  const pages = q.data?.pages;
  // Offset paging over a list that grows at the HEAD repeats a row: a message
  // that arrives between two page reads pushes its neighbour across the
  // boundary, and the same id comes back in both pages. Deduping here is what
  // keeps that from rendering twice under one React key.
  const items = useMemo(() => {
    const seen = new Set<string>();
    const out: Msg[] = [];
    for (const page of pages ?? []) {
      for (const m of page.messages) {
        if (seen.has(m.id)) continue;
        seen.add(m.id);
        out.push(m);
      }
    }
    return out;
  }, [pages]);
  const total = pages?.[0]?.total ?? 0;

  // "Check now" composes a fresh adaptive message server-side. The write does
  // not know what it produced, so it does not patch the list: it invalidates
  // it, and every page the reader has open is re-read. Awaiting the
  // invalidation is what keeps the button in its working state until the new
  // message is actually on screen.
  const checkNow = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/commute/reason", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkpoint: "during", dispatch: "no", force_llm: true }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: commuteKeys.messagesAll() }),
  });

  if (items.length === 0) {
    if (q.isPending) return <LoadingRows />;
    if (q.isError) {
      return (
        <StateBlock
          variant="error"
          title="Couldn't load messages"
          hint={<>The commute feed could not be read. <code>/api/commute/messages</code></>}
          action={<Button onClick={() => void q.refetch()}>Retry</Button>}
        />
      );
    }
  }

  const top = items[0];
  const rest = items.slice(1);

  return (
    <div>
      {q.isError && (
        <Notice tone="error" detail={errText(q.error)} action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}>
          The feed below is out of date.
        </Notice>
      )}
      {checkNow.isError && (
        <Notice tone="error" detail={errText(checkNow.error)}>The check did not run.</Notice>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>{total} message{total === 1 ? "" : "s"}</p>
        <Button onClick={() => checkNow.mutate()} disabled={checkNow.isPending}>
          <Sparkles size={14} aria-hidden /> {checkNow.isPending ? "Thinking…" : "Check now"}
        </Button>
      </div>

      {items.length === 0 ? (
        <StateBlock
          icon={<Inbox aria-hidden />}
          title="No commute messages yet"
          hint={<>They arrive automatically around your commute. Use <strong>Check now</strong> for an on-demand read.</>}
        />
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
          {q.hasNextPage && (
            <div style={{ textAlign: "center", marginTop: "0.9rem" }}>
              <Button onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
                {q.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
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

const STATUS_POLL_MS = 30_000;

export default function Commute() {
  const [tab, setTab] = useState<Tab>("now");
  const qc = useQueryClient();

  // ONE STATUS READ FOR THE WHOLE APP, AND ONLY WHERE IT IS RENDERED.
  //
  // There were two: this one at 30s and `useCommuteNative`'s at 60s, each with
  // its own copy of the brief. They are two observers of one cache entry now
  // (`commuteStatusQuery`), so the app makes one request per tick no matter how
  // many readers there are.
  //
  // `enabled` is the second half. The brief is painted by the Status tab and
  // nowhere else, and it was polled on every tab: four fifths of those requests
  // fetched a payload nobody could see. Leaving the tab stops this reader; it
  // does not stop the native one, because the phone's notification and Live
  // Activity must not depend on which tab is open.
  const statusQuery = useQuery(commuteStatusQuery<Brief>({
    enabled: tab === "status",
    refetchMs: STATUS_POLL_MS,
  }));

  // "Refresh transit data" re-fetches the upstream feeds server-side, then the
  // brief is invalidated rather than re-read by hand, so every reader of the
  // key (including the native one) sees the result of the poll.
  const pollNow = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/commute/poll", { method: "POST" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: commuteKeys.status() }),
  });

  const select = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  return (
    <div>
      <div style={{ marginBottom: "var(--space-5)" }}>
        <Tabs items={TABS.map((t) => ({ value: t.key, label: t.label }))} value={tab} onChange={select} />
      </div>

      {tab === "now" && <NowTab />}
      {tab === "status" && (
        <StatusTab
          brief={statusQuery.data ?? null}
          loading={statusQuery.isPending}
          error={statusQuery.isError ? errText(statusQuery.error) : null}
          pollError={pollNow.isError ? errText(pollNow.error) : null}
          onRetry={() => void statusQuery.refetch()}
          onPollNow={() => pollNow.mutate()}
          polling={pollNow.isPending}
        />
      )}
      {tab === "schedule" && <ScheduleTab />}
      {tab === "sources" && <SourcesTab />}
      {tab === "alerts" && <AlertsTab />}
    </div>
  );
}
