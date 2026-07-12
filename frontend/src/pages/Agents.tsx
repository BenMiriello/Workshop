import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Bot, Brain, Clock, PanelsTopLeft } from "lucide-react";
import type { Agent, BuildAgent, Project } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  Field,
  Input,
  Modal,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Stack,
  StateBlock,
  StatusDot,
  Tag,
  Toggle,
  type SegmentedOption,
  type StatusKind,
} from "../ui";

type SupervisorStatus = {
  supervisor: { paused: boolean; agent_count: number; paused_at: string | null };
  cron_autonomy: { disabled: boolean };
};

// Internal kind tokens stay "data"/"build" (Letta vs Claude Code wiring); the
// UI says "Assistant"/"Builder" everywhere — one name per concept (no more
// "data" leaking into the operator-facing surface).
type Kind = "all" | "data" | "build";

const KIND_OPTIONS: SegmentedOption<Kind>[] = [
  { value: "all", label: "All" },
  { value: "data", label: "Assistants" },
  { value: "build", label: "Builders" },
];

const NEW_PROJECT = "__new__";

const titleCase = (slug: string) =>
  slug.split("-").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// Recency label for a card: a time within the last 24h ("Today 3:42 PM"),
// otherwise a plain date ("Jun 18"). Empty when there's no usable timestamp.
function activityLabel(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const diff = Date.now() - d.getTime();
  if (diff >= 0 && diff < 24 * 3600 * 1000) {
    return `Today ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

const dataTs = (a: Agent) => a.last_active || a.created_at || "";
const buildTs = (a: BuildAgent) => a.last_active || a.created || "";

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

// "New builder" picks WHICH PROJECT and then offers the same Name/Slug/Purpose
// fields as the in-project "Spawn build agent" modal (ProjectDetail) — the two
// creation paths are identical apart from this picker. Name/slug default from
// the chosen project and the existing builders, but stay editable.
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
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [nameTouched, setNameTouched] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Default the picker to the first project once they load.
  useEffect(() => {
    if (open && !projectSlug && projects && projects.length) {
      setProjectSlug(projects[0].slug);
    }
  }, [open, projectSlug, projects]);

  // Defaults derived from the chosen project (only while the operator hasn't
  // typed over them). Switching projects re-derives the untouched fields.
  const defaults = useMemo(
    () =>
      projectSlug && projectSlug !== NEW_PROJECT
        ? deriveBuilderName(projectSlug, existing)
        : { slug: "", name: "" },
    [projectSlug, existing],
  );
  const effectiveName = nameTouched ? name : defaults.name;
  const effectiveSlug = slugTouched ? slug : slugify(effectiveName) || defaults.slug;

  const submit = async () => {
    if (projectSlug === NEW_PROJECT) {
      onNewProject();
      return;
    }
    if (!projectSlug || !effectiveSlug || !effectiveName.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/agents/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: effectiveSlug,
          name: effectiveName.trim(),
          purpose: purpose.trim(),
          project_slug: projectSlug,
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

  const noProjects = !!projects && projects.length === 0;
  const isNewProject = projectSlug === NEW_PROJECT;

  return (
    <Modal open={open} onClose={onCancel} title="New builder">
      <Stack>
        <p className="ws-muted">
          A builder is a Claude Code session scoped to a project. Pick the project,
          then name the builder (or keep the suggested name).
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
        {!isNewProject && !noProjects && (
          <>
            <Field label="Name" required>
              <Input
                value={effectiveName}
                onChange={(e) => { setName(e.target.value); setNameTouched(true); }}
              />
            </Field>
            <Field label="Slug" help="kebab-case · used as the agent's id">
              <Input
                value={effectiveSlug}
                onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
                spellCheck={false}
              />
            </Field>
            <Field label="Purpose" help="One sentence. What is this agent working on?">
              <Input
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="e.g. build the landing-page hero section"
              />
            </Field>
          </>
        )}
        {err && (
          <StateBlock variant="error" title="Couldn't start builder" hint={<code>{err}</code>} />
        )}
        <Row end>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={busy || !projectSlug || (!isNewProject && (noProjects || !effectiveSlug || !effectiveName.trim()))}
            loading={busy}
          >
            {isNewProject ? "New project…" : "Start builder"}
          </Button>
        </Row>
      </Stack>
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
  const [autonomyBusy, setAutonomyBusy] = useState(false);
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


  // Autonomy = the cron/Ralph background loop. Enabling clears the disable flag
  // (backend deletes .runtime/AUTONOMY_DISABLED); disabling re-creates it. This
  // is a live, current control — the cron scripts check this flag every tick.
  const setAutonomy = async (enabled: boolean) => {
    setAutonomyBusy(true);
    try {
      await fetch(`/api/agents/autonomy/${enabled ? "enable" : "disable"}`, { method: "POST" });
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

  const showData = kind === "all" || kind === "data";
  const showBuild = kind === "all" || kind === "build";
  const liveBuild = build.filter((a) => a.state !== "retired");
  const totalCount = (showData ? data.length : 0) + (showBuild ? liveBuild.length : 0);

  // One merged, recency-sorted list (most recent at top) across both kinds —
  // each row tagged with its kind so the card renders the right chrome.
  type Row =
    | { kind: "data"; ts: string; agent: Agent }
    | { kind: "build"; ts: string; agent: BuildAgent };
  const rows: Row[] = [
    ...(showData ? data.map((a): Row => ({ kind: "data", ts: dataTs(a), agent: a })) : []),
    ...(showBuild ? liveBuild.map((a): Row => ({ kind: "build", ts: buildTs(a), agent: a })) : []),
  ].sort((x, y) => (y.ts || "").localeCompare(x.ts || ""));

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
        // count. Autonomy: ONE toggle bound to the cron/Ralph background loop —
        // on clears .runtime/AUTONOMY_DISABLED, off re-creates it. (The old
        // separate supervisor pause/resume was a stale, confusing second
        // control; the cron flag is the live switch.)
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
                      {data.length} {data.length === 1 ? "assistant" : "assistants"}
                    </StatusDot>
                  </Row>
                </div>
              </Link>

              <div>
                <SectionLabel>Autonomy</SectionLabel>
                <Row>
                  <StatusDot status={autonomyOn ? "running" : "stopped"}>
                    {autonomyOn ? "On" : "Off"}
                  </StatusDot>
                  <Toggle
                    checked={autonomyOn}
                    disabled={autonomyBusy}
                    onChange={(next) => setAutonomy(next)}
                    label={<span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>background loop</span>}
                  />
                </Row>
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
        <CardGrid className="ws-agents-grid">
          {rows.map((r) => {
            // Categorical color is by KIND only — assistants share one hue,
            // builders another. (No more per-id rainbow on assistants.)
            const when = activityLabel(r.ts);
            if (r.kind === "data") {
              const a = r.agent;
              return (
                <li key={`data:${a.id}`}>
                  <Link to={`/agents/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                    <Card interactive className="ws-agent-card">
                      <Row between>
                        <h2 className="ws-card__title">
                          <Brain size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                          {a.name}
                        </h2>
                        <Tag hue="violet">assistant</Tag>
                      </Row>
                      <p className="ws-card__meta ws-mono">{a.id}</p>
                      <p className="ws-card__meta">memory: {a.memory_blocks.join(", ") || "—"}</p>
                      <p className="ws-card__meta ws-agent-card__when">
                        {when && <><Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{when}</>}
                      </p>
                    </Card>
                  </Link>
                </li>
              );
            }
            const a = r.agent;
            return (
              <li key={`build:${a.slug}`} className="ws-agent-card-cell">
                {/* Card body opens the agent's CHAT directly; the corner button
                    opens the full builder workbench (files/editor/preview/diff).
                    The button is a sibling of the Link (not nested) so it stays
                    valid HTML and its click doesn't trigger the card navigation. */}
                <Link to={`/chat/${encodeURIComponent(`build:${a.slug}`)}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card interactive className="ws-agent-card">
                    <Row between>
                      <h2 className="ws-card__title">
                        <Bot size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name}
                      </h2>
                      <Tag hue="turquoise">builder</Tag>
                    </Row>
                    <p className="ws-card__meta ws-mono">{a.slug}</p>
                    <p className="ws-card__meta">
                      {a.project_slug ? <>linked to <code>{a.project_slug}</code></> : "freestanding"}
                    </p>
                    <p className="ws-card__meta ws-agent-card__when">
                      {when && <><Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{when}</>}
                    </p>
                  </Card>
                </Link>
                <button
                  type="button"
                  className="ws-agent-card__workbench"
                  onClick={() => nav(`/agents/build/${a.slug}`)}
                  title="Open builder workbench (files · editor · preview · diff)"
                  aria-label="Open builder workbench"
                >
                  <PanelsTopLeft size={16} aria-hidden />
                </button>
              </li>
            );
          })}
        </CardGrid>
      )}

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
