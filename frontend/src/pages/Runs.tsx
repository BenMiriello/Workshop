import { useCallback, useEffect, useState } from "react";

type Run = {
  ts?: string;
  task_picked?: string;
  task?: string;
  exit_code?: number;
  duration_s?: number;
  agent_id?: string;
  notes?: string;
  [k: string]: unknown;
};

type RunsResp = {
  runs: Run[];
  count: number;
  log_path: string;
  note?: string;
};

const statusFor = (r: Run): { label: string; color: string } => {
  if (r.exit_code === 0) return { label: "ok", color: "#16a34a" };
  if (r.exit_code != null) return { label: `exit ${r.exit_code}`, color: "#ef4444" };
  return { label: "—", color: "var(--muted)" };
};

export default function Runs() {
  const [data, setData] = useState<RunsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/agents/runs?limit=50");
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
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!data) return <p>Loading runs…</p>;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted" style={{ margin: 0 }}>
          {data.count} {data.count === 1 ? "run" : "runs"}
          {data.note ? ` · ${data.note}` : ""}
        </p>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>

      <p className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", marginBottom: "1rem" }}>
        log: {data.log_path}
      </p>

      {data.runs.length === 0 ? (
        <p className="muted">
          No agent runs logged yet. The cron loop (Phase 5a) appends here once installed.
        </p>
      ) : (
        <div className="table-scroll">
        <table>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
              <th>Time</th>
              <th>Agent</th>
              <th>Task</th>
              <th>Status</th>
              <th style={{ textAlign: "right" }}>Duration</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r, i) => {
              const status = statusFor(r);
              return (
                <tr key={`${r.ts ?? "?"}-${i}`} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", whiteSpace: "nowrap" }}>{r.ts ?? "—"}</td>
                  <td className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>{r.agent_id ?? "—"}</td>
                  <td>{r.task_picked ?? r.task ?? r.notes ?? "—"}</td>
                  <td><span style={{ color: status.color, fontWeight: 600 }}>● {status.label}</span></td>
                  <td style={{ textAlign: "right" }} className="muted">{r.duration_s != null ? `${r.duration_s}s` : "—"}</td>
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
