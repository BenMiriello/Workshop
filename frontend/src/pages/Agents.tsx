import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Bot, Brain } from "lucide-react";
import type { Agent, BuildAgent, Project } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  ConfirmDialog,
  Field,
  Modal,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  Select,
  StateBlock,
  StatusDot,
  Tag,
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

const NEW_PROJECT = "__new__";

const titleCase = (slug: string) =>
  slug.split("-").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");

// Auto-generate a non-colliding builder slug `<project>-builder-<n>` and a readable
// name `<Project> builder <n>`. No name/slug/purpose is asked of the operator —
// the context (the chosen project) is the substance. See doc 01 §6.
function deriveBuilderName(projectSlug: string, existing: BuildAgent[]): { slug: string; name: string } {
  const taken = new Set(existing.map((a) => a.slug));
  let n = 1;
  while (taken.has(`${projectSlug}-builder-${n}`)) n += 1;
  return {
    slug: `${projectSlug}-builder-${n}`,
    name: `${titleCase(projectSlug)} builder ${n}`,
  };
}

// "New builder" asks WHICH PROJECT, not for a name/slug/purpose (doc 01 §6).
// On submit the builder is auto-named from the project and started there.
// "New project…" routes to the existing project-creation flow.
function NewBuilderModal({
  open, projects, existing, onCancel, onCreated, onNewProject,
}: {
  open: boolean;
  projects: Project[] | null;
  existing: BuildAgent[];
  onCancel: () => void;
  onCreated: (slug: string) => void;
  onNewProject: () => void;
}) {
  const [projectSlug, setProjectSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Default the picker to the first project once they load.
  useEffect(() => {
    if (open && !projectSlug && projects && projects.length) {
      setProjectSlug(projects[0].slug);
    }
  }, [open, projectSlug, projects]);

  const submit = async () => {
    if (projectSlug === NEW_PROJECT) {
      onNewProject();
      return;
    }
    if (!projectSlug) return;
    setBusy(true);
    setErr(null);
    try {
      const { slug, name } = deriveBuilderName(projectSlug, existing);
      const r = await fetch("/api/agents/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug, name, purpose: "", project_slug: projectSlug }),
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

  const noProjects = !!projects && projects.length === 0;

  return (
    <Modal open={open} onClose={onCancel} title="New builder">
      <div style={{ padding: "var(--space-4)" }}>
        <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
          A builder is a Claude Code session scoped to a project. Pick the project to
          work on; the builder is named automatically and you can rename it later.
        </p>
        <Field label="Project" required>
          <Select
            value={projectSlug}
            onChange={(e) => setProjectSlug(e.target.value)}
            disabled={busy || !projects}
            autoFocus
          >
            {!projects && <option value="">Loading…</option>}
            {noProjects && <option value="">No projects yet</option>}
            {projects?.map((p) => (
              <option key={p.slug} value={p.slug}>{p.title}</option>
            ))}
            <option value={NEW_PROJECT}>New project…</option>
          </Select>
        </Field>
        {err && (
          <div style={{ marginTop: "var(--space-3)" }}>
            <StateBlock variant="error" title="Couldn't start builder" hint={<code>{err}</code>} />
          </div>
        )}
        <div style={{ marginTop: "var(--space-4)", display: "flex", gap: "var(--space-3)", justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={busy || !projectSlug || (projectSlug !== NEW_PROJECT && noProjects)}
            loading={busy}
          >
            {projectSlug === NEW_PROJECT ? "New project…" : "Start builder"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function Agents() {
  const nav = useNavigate();
  const [data, setData] = useState<Agent[] | null>(null);
  const [build, setBuild] = useState<BuildAgent[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [autonomyBusy, setAutonomyBusy] = useState(false);
  const [confirmPause, setConfirmPause] = useState(false);
  const [kind, setKind] = useState<Kind>("all");
  const [spawning, setSpawning] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [dR, bR, pR] = await Promise.all([
        fetch("/api/agents/"),
        fetch("/api/agents/build"),
        fetch("/api/projects/"),
      ]);
      if (!dR.ok) throw new Error(`${dR.status} ${await dR.text()}`);
      setData(await dR.json());
      setBuild(bR.ok ? await bR.json() : []);
      setProjects(pR.ok ? await pR.json() : []);
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

  const enableAutonomy = async () => {
    setAutonomyBusy(true);
    try {
      await fetch("/api/autonomy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: true }),
      });
      loadSup();
    } finally {
      setAutonomyBusy(false);
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
          <Row wrap>
            <Segmented options={KIND_OPTIONS} value={kind} onChange={setKind} ariaLabel="Agent kind" />
            <Link to="/agents/new">
              <Button variant="primary" size="sm">
                <Plus aria-hidden /> New assistant
              </Button>
            </Link>
            <Button variant="primary" size="sm" onClick={() => setSpawning(true)}>
              <Plus aria-hidden /> New builder
            </Button>
          </Row>
        }
      />

      {sup && (() => {
        // At-a-glance status strip: three labeled segments (doc 01 §7).
        // Builders: a live/idle split (running == live). Assistants: a plain
        // count (request/response — "idle" is meaningless). Autonomy: the
        // background-loop state with exactly ONE clearly-labeled control.
        const assistantCount = sup.supervisor.agent_count;
        const liveBuilds = build.filter((a) => a.state === "running").length;
        const idleBuilds = build.filter((a) => a.state === "idle").length;
        const autonomyOn = !sup.cron_autonomy.disabled;
        // Dot agrees with the text: running only when something is actually live.
        const builderDot: StatusKind = liveBuilds > 0 ? "running" : "ok";

        return (
          <Card style={{ marginBottom: "var(--space-5)", padding: "var(--space-4)" }}>
            <Row between wrap style={{ rowGap: "var(--space-4)" }}>
              <Link to="/agents?kind=build" onClick={() => setKind("build")} style={{ textDecoration: "none", color: "inherit" }}>
                <div>
                  <SectionLabel>Builders</SectionLabel>
                  <Row>
                    <StatusDot status={builderDot}>
                      {liveBuilds} live · {idleBuilds} idle
                    </StatusDot>
                  </Row>
                </div>
              </Link>

              <Link to="/agents?kind=data" onClick={() => setKind("data")} style={{ textDecoration: "none", color: "inherit" }}>
                <div>
                  <SectionLabel>Assistants</SectionLabel>
                  <Row>
                    <StatusDot status="ok">
                      {assistantCount} active
                    </StatusDot>
                  </Row>
                </div>
              </Link>

              <div>
                <SectionLabel>Autonomy</SectionLabel>
                {!autonomyOn ? (
                  <Row>
                    <StatusDot status="stopped">Off</StatusDot>
                    <Button size="sm" variant="primary" loading={autonomyBusy} onClick={enableAutonomy}>
                      Enable
                    </Button>
                  </Row>
                ) : paused ? (
                  <Row>
                    <StatusDot status="warn">Paused</StatusDot>
                    <Button size="sm" variant="primary" loading={busy} onClick={() => setPaused(false)}>
                      Resume
                    </Button>
                  </Row>
                ) : (
                  <Row>
                    <StatusDot status="running">On</StatusDot>
                    <Button size="sm" variant="danger-ghost" disabled={busy} onClick={() => setConfirmPause(true)}>
                      Pause
                    </Button>
                  </Row>
                )}
              </div>
            </Row>
          </Card>
        );
      })()}

      {totalCount === 0 ? (
        <StateBlock
          title="No agents yet"
          hint={
            <span>
              Use <strong>+ New assistant</strong> for an in-app worker, or
              <strong> + New builder</strong> to start a Claude Code session on a project.
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

      <NewBuilderModal
        open={spawning}
        projects={projects}
        existing={build}
        onCancel={() => setSpawning(false)}
        onCreated={(slug) => {
          setSpawning(false);
          nav(`/agents/build/${slug}`);
        }}
        onNewProject={() => {
          setSpawning(false);
          nav("/projects/new");
        }}
      />
    </div>
  );
}
