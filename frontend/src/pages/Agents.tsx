import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Bot, Brain } from "lucide-react";
import type { Agent, BuildAgent } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  ConfirmDialog,
  Field,
  Input,
  Modal,
  PageHeader,
  Row,
  Segmented,
  StateBlock,
  StatusPill,
  Tag,
  Textarea,
  catFor,
  type SegmentedOption,
  type StatusKind,
} from "../ui";

type SupervisorStatus = {
  supervisor: { paused: boolean; agent_count: number; paused_at: string | null };
  cron_autonomy: { disabled: boolean };
};

type Kind = "all" | "data" | "build";

const KIND_OPTIONS: SegmentedOption<Kind>[] = [
  { value: "all", label: "All" },
  { value: "data", label: "Data" },
  { value: "build", label: "Build" },
];

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// Two kinds of Agent per ADR-008:
//   - Data agents (Letta-backed)     → existing fleet, narrow read/write tools
//   - Build agents (Claude Code)     → coding sessions scoped to a cwd
// This page lists both, with a kind filter and separate spawn CTAs.

function SpawnBuildAgentModal({
  open, onCancel, onCreated,
}: { open: boolean; onCancel: () => void; onCreated: (slug: string) => void }) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const effectiveSlug = useMemo(
    () => (slugTouched ? slug : slugify(name)),
    [slug, slugTouched, name],
  );

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/agents/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: effectiveSlug,
          name: name.trim() || effectiveSlug,
          purpose: purpose.trim(),
        }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const a = await r.json();
      onCreated(a.slug);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onCancel} title="Spawn freestanding Build agent">
      <div style={{ padding: "var(--space-4)" }}>
        <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
          Opens a Claude Code coding session scoped to a fresh scratch directory under
          <code className="ws-mono"> ~/workshop/build-agents/&lt;slug&gt;/</code>.
          To work on an existing Project instead, open that Project and use "Spawn build agent" there.
        </p>
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Slug" help="kebab-case · used as the agent id + dir name">
          <Input
            value={effectiveSlug}
            onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
            spellCheck={false}
          />
        </Field>
        <Field label="Purpose" help="What is this agent working on?">
          <Textarea
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            rows={4}
            placeholder="e.g. prototype a tip calculator that consumes Finance data"
          />
        </Field>
        {err && <p style={{ color: "var(--err)", fontSize: "var(--fs-sm)", marginTop: "var(--space-2)" }}>{err}</p>}
        <div style={{ marginTop: "var(--space-4)", display: "flex", gap: "var(--space-3)", justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy || !effectiveSlug || !name.trim()} loading={busy}>
            Spawn
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function Agents() {
  const [data, setData] = useState<Agent[] | null>(null);
  const [build, setBuild] = useState<BuildAgent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmPause, setConfirmPause] = useState(false);
  const [kind, setKind] = useState<Kind>("all");
  const [spawning, setSpawning] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [dR, bR] = await Promise.all([
        fetch("/api/agents/"),
        fetch("/api/agents/build"),
      ]);
      if (!dR.ok) throw new Error(`${dR.status} ${await dR.text()}`);
      setData(await dR.json());
      setBuild(bR.ok ? await bR.json() : []);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const loadSup = useCallback(() => {
    fetch("/api/agents/autonomy/status")
      .then((r) => (r.ok ? r.json() : null))
      .then(setSup)
      .catch(() => setSup(null));
  }, []);

  useEffect(() => {
    loadAll();
    loadSup();
    const t = setInterval(loadSup, 15_000);
    return () => clearInterval(t);
  }, [loadAll, loadSup]);

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
  if (!data || !build) {
    return <div><PageHeader title="Agents" sub="Loading…" /></div>;
  }

  const paused = !!sup?.supervisor.paused;
  const showData = kind === "all" || kind === "data";
  const showBuild = kind === "all" || kind === "build";
  const totalCount =
    (showData ? data.length : 0) + (showBuild ? build.filter((a) => a.state !== "retired").length : 0);

  return (
    <div>
      <PageHeader
        title="Agents"
        sub={`${totalCount} agent${totalCount === 1 ? "" : "s"}`}
        actions={
          <Row>
            <Segmented options={KIND_OPTIONS} value={kind} onChange={setKind} ariaLabel="Agent kind" />
            <Link to="/agents/new">
              <Button variant="primary" size="sm">
                <Plus aria-hidden /> New data agent
              </Button>
            </Link>
            <Button variant="primary" size="sm" onClick={() => setSpawning(true)}>
              <Plus aria-hidden /> Spawn build agent
            </Button>
          </Row>
        }
      />

      {(() => {
        if (!sup) return null;
        const dataCount = sup.supervisor.agent_count;
        const liveBuilds = build.filter((a) => a.state === "running").length;
        const idleBuilds = build.filter((a) => a.state === "idle").length;
        // Hide the card entirely when there's nothing to say.
        if (dataCount === 0 && liveBuilds === 0 && idleBuilds === 0) return null;

        let line1: { kind: StatusKind; text: string };
        if (paused) {
          line1 = { kind: "danger", text: "Supervisor paused" };
        } else if (dataCount === 0) {
          line1 = { kind: "ok", text: "0 data agents" };
        } else {
          line1 = { kind: "ok", text: `${dataCount} data agent${dataCount === 1 ? "" : "s"} idle` };
        }

        return (
          <Card style={{ marginBottom: "var(--space-5)", padding: "var(--space-3) var(--space-4)" }}>
            <Row between wrap>
              <div>
                <Row>
                  <StatusPill status={line1.kind}>{line1.text}</StatusPill>
                  {sup.cron_autonomy.disabled && (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      Background loop off ·{" "}
                      <button
                        type="button"
                        onClick={() => fetch("/api/autonomy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: true }) }).then(loadSup)}
                        style={{ background: "none", border: 0, color: "var(--accent)", cursor: "pointer", padding: 0, font: "inherit" }}
                      >
                        Enable
                      </button>
                    </span>
                  )}
                </Row>
                {(liveBuilds > 0 || idleBuilds > 0) && (
                  <Row style={{ marginTop: "var(--space-2)" }}>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                      {liveBuilds} build session{liveBuilds === 1 ? "" : "s"} live · {idleBuilds} idle
                    </span>
                  </Row>
                )}
              </div>
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
        );
      })()}

      {totalCount === 0 ? (
        <StateBlock
          title="No agents yet"
          hint={
            <span>
              Use <strong>+ New data agent</strong> for a Letta-backed worker, or
              <strong> + Spawn build agent</strong> for a Claude Code coding session.
            </span>
          }
        />
      ) : (
        <CardGrid>
          {showData && data.map((a) => (
            <li key={`data:${a.id}`}>
              <Link to={`/agents/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between>
                    <h2 className="ws-card__title">
                      <Brain size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {a.name}
                    </h2>
                    <Tag hue={catFor(a.id)}>data</Tag>
                  </Row>
                  <p className="ws-card__meta ws-mono">{a.id}</p>
                  <p className="ws-card__meta">memory: {a.memory_blocks.join(", ") || "—"}</p>
                </Card>
              </Link>
            </li>
          ))}
          {showBuild && build.filter((a) => a.state !== "retired").map((a) => (
            <li key={`build:${a.slug}`}>
              <Link to={`/agents/build/${a.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between>
                    <h2 className="ws-card__title">
                      <Bot size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {a.name}
                    </h2>
                    <Tag hue="turquoise">build</Tag>
                  </Row>
                  <p className="ws-card__meta ws-mono">{a.slug}</p>
                  <p className="ws-card__meta">
                    {a.project_slug ? <>linked to <code>{a.project_slug}</code></> : "freestanding"}
                  </p>
                  {a.purpose && <p style={{ marginTop: "var(--space-2)" }}>{a.purpose}</p>}
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
        body="All autonomous Data-agent ticks stop until you resume. In-flight work finishes; nothing new is picked up. Build agents are unaffected (they only run when you message them)."
        confirmLabel="Pause supervisor"
        busy={busy}
      />

      <SpawnBuildAgentModal
        open={spawning}
        onCancel={() => setSpawning(false)}
        onCreated={(slug) => {
          setSpawning(false);
          window.location.assign(`/agents/build/${slug}`);
        }}
      />
    </div>
  );
}
