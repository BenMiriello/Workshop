import { useCallback, useEffect, useState } from "react";

// Commute · Config panel
// -----------------------------------------------------------------------------
// Moved here from the Workshop frontend (was at /commute). The day-of view and
// the config UI both live in the same app now — Workshop has no commute tab.
//
// All edits hit the shared workshop backend at /api/commute/* (proxied by vite
// dev/preview to 127.0.0.1:8000). No new backend routes; this is purely a UI
// relocation.

type SourceStatus = Record<string, { status: string; ts: string; note?: string; entity_count?: number }>;

type Schedule = {
  operator?: string;
  home?: { address?: string };
  work?: { address?: string };
  schedules: ScheduleEntry[];
  notes?: string;
};

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
  legs_id: string;
};

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export default function Config() {
  const [sources, setSources] = useState<{ registry: Record<string, unknown>; status: SourceStatus } | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [targets, setTargets] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [sub, setSub] = useState<"schedule" | "targets" | "sources">("schedule");

  const fetchAll = useCallback(async () => {
    try {
      const [sRes, schRes, tRes] = await Promise.all([
        fetch("/api/commute/sources"),
        fetch("/api/commute/schedule"),
        fetch("/api/commute/targets"),
      ]);
      if (sRes.ok) setSources(await sRes.json());
      if (schRes.ok) setSchedule(await schRes.json());
      if (tRes.ok) setTargets(JSON.stringify(await tRes.json(), null, 2));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  return (
    <div>
      {err && <div className="err" style={{ marginBottom: "0.75rem" }}>{err}</div>}

      <div style={{ display: "flex", gap: "0.25rem", marginBottom: "1rem", borderBottom: "1px solid var(--border)" }}>
        {(["schedule", "targets", "sources"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setSub(t)}
            style={{
              background: sub === t ? "var(--card)" : "transparent",
              color: sub === t ? "var(--accent)" : "var(--muted)",
              fontWeight: sub === t ? 600 : 400,
              border: "none",
              padding: "0.5rem 0.85rem",
              borderRadius: "6px 6px 0 0",
              cursor: "pointer",
              fontSize: "0.95rem",
              textTransform: "capitalize",
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {sub === "schedule" && <SchedulePanel schedule={schedule} onSaved={fetchAll} />}
      {sub === "targets" && <TargetsPanel initial={targets} onSaved={fetchAll} />}
      {sub === "sources" && <SourcesPanel sources={sources} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schedule editor — POSTs to /api/commute/schedule
// ---------------------------------------------------------------------------
function SchedulePanel({ schedule, onSaved }: { schedule: Schedule | null; onSaved: () => void }) {
  const [draft, setDraft] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (schedule) setDraft(JSON.stringify(schedule, null, 2));
  }, [schedule]);

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const parsed = JSON.parse(draft);
      const r = await fetch("/api/commute/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: parsed }),
      });
      if (!r.ok) throw new Error(`http ${r.status}: ${await r.text()}`);
      setMsg("saved");
      onSaved();
    } catch (e) {
      setMsg(`error: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  if (!schedule) return <div>loading…</div>;
  return (
    <div className="card">
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        style={{
          width: "100%",
          minHeight: "320px",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "0.85rem",
          background: "var(--card)",
          color: "var(--fg)",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          padding: "0.75rem",
          boxSizing: "border-box",
        }}
      />
      <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button onClick={save} disabled={saving}>{saving ? "saving…" : "save"}</button>
        {msg && <span className="muted" style={{ color: msg.startsWith("error") ? "var(--danger)" : undefined }}>{msg}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Targets editor — POSTs to /api/commute/targets
// ---------------------------------------------------------------------------
function TargetsPanel({ initial, onSaved }: { initial: string; onSaved: () => void }) {
  const [draft, setDraft] = useState<string>(initial);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { setDraft(initial); }, [initial]);

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const parsed = JSON.parse(draft);
      const r = await fetch("/api/commute/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: parsed }),
      });
      if (!r.ok) throw new Error(`http ${r.status}: ${await r.text()}`);
      setMsg("saved");
      onSaved();
    } catch (e) {
      setMsg(`error: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  if (!draft) return <div>loading…</div>;
  return (
    <div className="card">
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        style={{
          width: "100%",
          minHeight: "260px",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "0.85rem",
          background: "var(--card)",
          color: "var(--fg)",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          padding: "0.75rem",
          boxSizing: "border-box",
        }}
      />
      <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button onClick={save} disabled={saving}>{saving ? "saving…" : "save"}</button>
        {msg && <span className="muted" style={{ color: msg.startsWith("error") ? "var(--danger)" : undefined }}>{msg}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sources panel — read-only view of the registry + last poll status
// ---------------------------------------------------------------------------
function SourcesPanel({ sources }: { sources: { registry: Record<string, unknown>; status: SourceStatus } | null }) {
  if (!sources) return <div>loading…</div>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const registry = (sources.registry as any)?.sources || [];
  return (
    <div className="card">
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "0.4rem 0.5rem" }}>source</th>
              <th style={{ padding: "0.4rem 0.5rem" }}>kind</th>
              <th style={{ padding: "0.4rem 0.5rem" }}>last status</th>
              <th style={{ padding: "0.4rem 0.5rem" }}>last poll</th>
              <th style={{ padding: "0.4rem 0.5rem" }}>entities</th>
              <th style={{ padding: "0.4rem 0.5rem" }}>notes</th>
            </tr>
          </thead>
          <tbody>
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            {registry.map((s: any) => {
              const st = sources.status[s.key];
              return (
                <tr key={s.key} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "0.4rem 0.5rem", fontFamily: "monospace" }}>{s.key}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }}>{s.kind}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }}>
                    {st ? <Pill status={st.status} /> : <span className="muted">never polled</span>}
                  </td>
                  <td style={{ padding: "0.4rem 0.5rem" }} className="muted">{st?.ts ? fmtAge(st.ts) : "—"}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }} className="muted">{st?.entity_count ?? "—"}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }} className="muted">{s.notes || st?.note || s.fallback || ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Pill({ status }: { status: string }) {
  const bg = status === "ok" ? "var(--ok)" : status === "needs_key" ? "var(--warn)" : status === "missing_deps" ? "#805ad5" : "var(--muted)";
  return <span style={{ background: bg, color: "white", padding: "0.1rem 0.5rem", borderRadius: "999px", fontSize: "0.78rem", fontWeight: 600 }}>{status}</span>;
}
