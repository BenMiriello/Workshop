import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus } from "lucide-react";
import type { Agent } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  ConfirmDialog,
  PageHeader,
  Row,
  StateBlock,
  StatusPill,
  Tag,
  catFor,
} from "../ui";

type SupervisorStatus = {
  supervisor: { paused: boolean; agent_count: number; paused_at: string | null };
  cron_autonomy: { disabled: boolean };
};

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmPause, setConfirmPause] = useState(false);

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
      .then((r) => (r.ok ? r.json() : null))
      .then(setSup)
      .catch(() => setSup(null));
  }, []);

  useEffect(() => {
    loadAgents();
    loadSup();
    const t = setInterval(loadSup, 15_000);
    return () => clearInterval(t);
  }, [loadAgents, loadSup]);

  const setPaused = async (pause: boolean) => {
    setBusy(true);
    try {
      await fetch(pause ? "/api/agents/supervisor/pause" : "/api/agents/supervisor/resume", { method: "POST" });
      loadSup();
    } finally {
      setBusy(false);
      setConfirmPause(false);
    }
  };

  if (err) {
    return (
      <div>
        <PageHeader title="Agents" />
        <StateBlock variant="error" title="Couldn't load agents" hint={<code>/api/agents/</code>} />
      </div>
    );
  }
  if (!agents) {
    return (
      <div>
        <PageHeader title="Agents" sub="Loading…" />
      </div>
    );
  }

  const paused = !!sup?.supervisor.paused;

  return (
    <div>
      <PageHeader
        title="Agents"
        sub={`${agents.length} agent${agents.length === 1 ? "" : "s"}`}
        actions={
          <Link to="/agents/new">
            <Button variant="primary" size="sm">
              <Plus aria-hidden /> New agent
            </Button>
          </Link>
        }
      />

      {sup && (
        <Card style={{ marginBottom: "var(--space-5)", padding: "var(--space-3) var(--space-4)" }}>
          <Row between wrap>
            <Row>
              <StatusPill status={paused ? "danger" : "running"}>
                {paused ? "paused" : `ticking ${sup.supervisor.agent_count} agent${sup.supervisor.agent_count === 1 ? "" : "s"}`}
              </StatusPill>
              {sup.cron_autonomy.disabled && <span className="ws-muted">· cron loop disabled</span>}
            </Row>
            {paused ? (
              <Button size="sm" variant="primary" loading={busy} onClick={() => setPaused(false)}>
                Resume
              </Button>
            ) : (
              <Button size="sm" variant="danger-ghost" disabled={busy} onClick={() => setConfirmPause(true)}>
                Pause
              </Button>
            )}
          </Row>
        </Card>
      )}

      {agents.length === 0 ? (
        <StateBlock
          title="No agents yet"
          hint="Run the memory migration to seed masini, or create one above."
        />
      ) : (
        <CardGrid>
          {agents.map((a) => (
            <li key={a.id}>
              <Link to={`/agents/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between>
                    <h2 className="ws-card__title">{a.name}</h2>
                    <Tag hue={catFor(a.id)}>agent</Tag>
                  </Row>
                  <p className="ws-card__meta ws-mono">{a.id}</p>
                  <p className="ws-card__meta">memory: {a.memory_blocks.join(", ") || "—"}</p>
                </Card>
              </Link>
            </li>
          ))}
        </CardGrid>
      )}

      <ConfirmDialog
        open={confirmPause}
        onCancel={() => setConfirmPause(false)}
        onConfirm={() => setPaused(true)}
        title="Pause the supervisor?"
        body="All autonomous agent ticks stop until you resume. In-flight work finishes; nothing new is picked up."
        confirmLabel="Pause supervisor"
        busy={busy}
      />
    </div>
  );
}
