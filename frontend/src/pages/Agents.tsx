import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Agent } from "@shared/types";

type SupervisorStatus = {
  supervisor: { paused: boolean; agent_count: number; paused_at: string | null };
  cron_autonomy: { disabled: boolean };
};

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const loadAgents = useCallback(() => {
    fetch("/api/agents/")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then(setAgents)
      .catch((e) => setErr(String(e)));
  }, []);

  const loadSup = useCallback(() => {
    fetch("/api/agents/autonomy/status")
      .then((r) => r.ok ? r.json() : null)
      .then(setSup)
      .catch(() => setSup(null));
  }, []);

  useEffect(() => {
    loadAgents();
    loadSup();
    const t = setInterval(loadSup, 15_000);
    return () => clearInterval(t);
  }, [loadAgents, loadSup]);

  const onToggle = async () => {
    if (!sup) return;
    const paused = sup.supervisor.paused;
    setBusy(true);
    try {
      await fetch(paused ? "/api/agents/supervisor/resume" : "/api/agents/supervisor/pause", { method: "POST" });
      loadSup();
    } finally {
      setBusy(false);
    }
  };

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!agents) return <p>Loading agents…</p>;

  const paused = !!sup?.supervisor.paused;

  return (
    <div>
      {sup && (
        <div style={{
          marginBottom: "1rem",
          padding: "0.55rem 0.75rem",
          border: "1px solid var(--border)",
          borderLeft: `3px solid ${paused ? "var(--err)" : "#16a34a"}`,
          background: "var(--card)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: "0.88rem",
        }}>
          <span>
            Supervisor: <strong style={{ color: paused ? "var(--err)" : "#16a34a" }}>{paused ? "paused" : `ticking ${sup.supervisor.agent_count} agent${sup.supervisor.agent_count === 1 ? "" : "s"}`}</strong>
            {sup.cron_autonomy.disabled && <span className="muted" style={{ marginLeft: "0.6rem" }}>· cron loop disabled</span>}
          </span>
          <button
            onClick={onToggle}
            disabled={busy}
            style={{ padding: "0.2rem 0.7rem", fontSize: "0.8rem", color: paused ? undefined : "var(--err)" }}
          >
            {busy ? "…" : (paused ? "resume" : "pause")}
          </button>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p className="muted">{agents.length} agent{agents.length === 1 ? "" : "s"}</p>
        <Link to="/agents/new"><button>+ New agent</button></Link>
      </div>
      {agents.length === 0 ? (
        <p>No agents yet. Run the memory migration to seed masini, or create one above.</p>
      ) : (
        <ul className="cards">
          {agents.map((a) => (
            <li key={a.id} className="card">
              <Link to={`/agents/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                <h2>{a.name}</h2>
                <p className="muted">{a.id}</p>
                <p className="muted">memory: {a.memory_blocks.join(", ") || "—"}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
