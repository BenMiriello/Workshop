import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Agent } from "@shared/types";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/agents/")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then(setAgents)
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!agents) return <p>Loading agents…</p>;

  return (
    <div>
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
