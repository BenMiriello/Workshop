import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Bot, Brain, Clock, SlidersHorizontal, ListChecks, Play, RotateCw, Power, Gauge, Compass, FileStack } from "lucide-react";
import type { ReactNode } from "react";
import type { BuildAgent, Project } from "@shared/types";
import {
  Badge,
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
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  Tag,
  Toggle,
  catFor,
  type StatusKind,
} from "../ui";
import { triggerSummary, nextScheduled, type UnifiedAgent } from "../lib/agentTriggers";
import { latitudeDesc, type Latitude } from "../lib/tasks";

// Roster item enriched with the O6/O7 autonomy fields the backend now returns on
// GET /api/agents/ (autonomy preset + role + depth + latitude). Kept local so the shared
// UnifiedAgent type is untouched; every existing UnifiedAgent field still applies.
type Preset = "off" | "on-demand" | "scheduled" | "continuous";
type RosterAgent = UnifiedAgent & {
  autonomy?: Preset;
  role?: string | null;
  depth?: "quick" | "standard" | "deep";
  fanout?: "off" | "read-only";
  // null = this agent scope sets no latitude, so its work inherits the default.
  // The chip is omitted in that case rather than rendering "standard": an agent
  // that never chose a latitude must not look like one that chose the default.
  latitude?: Latitude | null;
};

function presetIcon(p: Preset): ReactNode {
  const props = { size: 12, strokeWidth: 1.8, "aria-hidden": true, style: { verticalAlign: "middle", marginRight: "0.3rem" } };
  if (p === "continuous") return <RotateCw {...props} />;
  if (p === "scheduled") return <Clock {...props} />;
  if (p === "on-demand") return <Play {...props} />;
  return <Power {...props} />;
}
import AutonomyControls from "../components/AutonomyControls";

type SupervisorStatus = {
  cron_autonomy: { disabled: boolean };
};

type Job = { id: string; agent_id?: string; status?: string; started_at?: string };

const NEW_PROJECT = "__new__";

const titleCase = (slug: string) =>
  slug.split("-").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// Recency label for a card: within the last 24h ("Today 3:42 PM"), else a plain
// date ("Jun 18"). Empty when there's no usable timestamp.
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

// Auto-generate a non-colliding builder slug + readable name for the modal.
function deriveBuilderName(projectSlug: string, existing: BuildAgent[]): { slug: string; name: string } {
  const taken = new Set(existing.map((a) => a.slug));
  let n = 1;
  while (taken.has(`${projectSlug}-builder-${n}`)) n += 1;
  return { slug: `${projectSlug}-builder-${n}`, name: `${titleCase(projectSlug)} builder ${n}` };
}

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

  useEffect(() => {
    if (open && !projectSlug && projects && projects.length) {
      setProjectSlug(projects[0].slug);
    }
  }, [open, projectSlug, projects]);

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
    if (projectSlug === NEW_PROJECT) { onNewProject(); return; }
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
    <Modal open={open} onClose={onCancel} title="New agent">
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
              <Input value={effectiveName} onChange={(e) => { setName(e.target.value); setNameTouched(true); }} />
            </Field>
            <Field label="Slug" help="kebab-case · used as the agent's id">
              <Input
                value={effectiveSlug}
                onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
                spellCheck={false}
              />
            </Field>
            <Field label="Purpose" help="One sentence. What is this agent working on?">
              <Input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. build the landing-page hero section" />
            </Field>
          </>
        )}
        {err && <StateBlock variant="error" title="Couldn't start agent" hint={<code>{err}</code>} />}
        <Row end>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={busy || !projectSlug || (!isNewProject && (noProjects || !effectiveSlug || !effectiveName.trim()))}
            loading={busy}
          >
            {isNewProject ? "New project…" : "Start agent"}
          </Button>
        </Row>
      </Stack>
    </Modal>
  );
}

// --- Templates + groups (P4) ------------------------------------------------
// A template is a file, not a registered agent: nothing in the list below is
// running or dispatched. Picking one FORKS it into a real agent, and the fork is
// independent — there is no sync back, by design.

type AgentTemplate = {
  template: string;
  template_version: number;
  description: string;
  name: string;
  role: string | null;
  purpose: string;
  triggers: UnifiedAgent["triggers"];
};
type TemplateGroup = {
  name: string;
  description: string;
  members: { template: string }[];
  scope_defaults?: { autonomy?: string; depth?: string };
};
type TemplateCatalog = { templates: AgentTemplate[]; groups: TemplateGroup[] };

function FromTemplateModal({
  open, projects, onCancel, onDone,
}: {
  open: boolean;
  projects: Project[] | null;
  onCancel: () => void;
  onDone: (slug: string | null) => void;
}) {
  const [cat, setCat] = useState<TemplateCatalog | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"templates" | "groups">("templates");
  const [pick, setPick] = useState<string>("");
  const [projectSlug, setProjectSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCat(null);
    setLoadErr(null);
    fetch("/api/agents/templates")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then((c: TemplateCatalog) => setCat(c))
      .catch((e) => setLoadErr(String(e)));
  }, [open]);

  const items = cat ? (tab === "templates" ? cat.templates : cat.groups) : [];
  const keyOf = (i: AgentTemplate | TemplateGroup) =>
    "template" in i ? i.template : i.name;

  const submit = async () => {
    if (!pick) return;
    setBusy(true);
    setErr(null);
    try {
      const body = tab === "templates"
        ? { template: pick, project: projectSlug || null }
        : { group: pick, project: projectSlug || null };
      const r = await fetch(`/api/agents/${tab === "templates" ? "from-template" : "adopt-group"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const res = await r.json();
      if (tab === "groups") {
        const skipped = (res.skipped || []) as { template: string; reason: string }[];
        if (skipped.length && !(res.adopted || []).length) {
          setErr(skipped.map((s) => `${s.template}: ${s.reason}`).join("; "));
          return;
        }
        onDone(null);
      } else {
        onDone(res.slug);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onCancel} title="Create from template" size="lg">
      <Stack>
        <p className="ws-muted">
          A template is a starting definition, not a subscription. Picking one copies
          it into a real agent you own and can edit; later changes to the template do
          not reach the copy.
        </p>
        <Segmented
          value={tab}
          onChange={(v) => { setTab(v as "templates" | "groups"); setPick(""); }}
          options={[
            { value: "templates", label: "Templates" },
            { value: "groups", label: "Groups" },
          ]}
        />
        {loadErr ? (
          <StateBlock
            variant="error"
            title="Couldn't load templates"
            hint={<code>/api/agents/templates</code>}
          />
        ) : !cat ? (
          <Stack><Skeleton /><Skeleton /><Skeleton /></Stack>
        ) : items.length === 0 ? (
          <StateBlock
            title={`No ${tab} shipped`}
            hint={<span>Add a definition to <code>agents/_templates/</code> (or a group JSON to <code>agents/_templates/groups/</code>) and it appears here.</span>}
          />
        ) : (
          // Overflow: the catalog is small by design (two groups, not five), but
          // it scrolls rather than growing the dialog past the viewport.
          <div style={{ maxHeight: "22rem", overflowY: "auto" }}>
            <Stack>
              {items.map((i) => {
                const k = keyOf(i);
                const selected = pick === k;
                return (
                  <Card
                    key={k}
                    interactive
                    onClick={() => setPick(k)}
                    aria-pressed={selected}
                    style={selected ? { outline: "2px solid var(--accent)" } : undefined}
                  >
                    <Row between>
                      <h2 className="ws-card__title">
                        <FileStack size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {"template" in i ? i.name : i.name}
                      </h2>
                      {"template" in i
                        ? i.role && <Tag hue={catFor(`role:${i.role}`)}>{i.role}</Tag>
                        : <Badge>{i.members.length} {i.members.length === 1 ? "agent" : "agents"}</Badge>}
                    </Row>
                    <p className="ws-card__meta">{i.description}</p>
                    {"template" in i ? (
                      <p className="ws-card__meta ws-muted">{triggerSummary(i.triggers)} · v{i.template_version}</p>
                    ) : (
                      <p className="ws-card__meta ws-muted">
                        {i.members.length
                          ? i.members.map((m) => m.template).join(" · ")
                          : "no resident agents"}
                      </p>
                    )}
                  </Card>
                );
              })}
            </Stack>
          </div>
        )}
        <Field label="Scope" help="Which scope the agent lives in and reads notes for.">
          <Select value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)} disabled={busy}>
            <option value="">Workshop (user scope)</option>
            {projects?.map((p) => (
              <option key={p.slug} value={p.slug}>{p.title}</option>
            ))}
          </Select>
        </Field>
        {err && <StateBlock variant="error" title="Couldn't create from template" hint={<code>{err}</code>} />}
        <Row end>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy || !pick} loading={busy}>
            {tab === "templates" ? "Create agent" : "Adopt group"}
          </Button>
        </Row>
      </Stack>
    </Modal>
  );
}

export default function Agents() {
  const nav = useNavigate();
  const [agents, setAgents] = useState<RosterAgent[] | null>(null);
  const [build, setBuild] = useState<BuildAgent[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [autonomyBusy, setAutonomyBusy] = useState(false);
  const [spawning, setSpawning] = useState(false);
  const [templating, setTemplating] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [aR, bR, pR, jR] = await Promise.all([
        fetch("/api/agents/"),
        fetch("/api/agents/build"),
        fetch("/api/projects/"),
        fetch("/api/jobs?limit=200"),
      ]);
      if (!aR.ok) throw new Error(`${aR.status} ${await aR.text()}`);
      setAgents((await aR.json()) as RosterAgent[]);
      setBuild(bR.ok ? await bR.json() : []);
      setProjects(pR.ok ? await pR.json() : []);
      setJobs(jR.ok ? (await jR.json()).jobs || [] : []);
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

  // Autonomy = the cron/Ralph background loop. Enabling clears the disable flag;
  // disabling re-creates it. Live control, the cron scripts check it each tick.
  const setAutonomy = async (enabled: boolean) => {
    setAutonomyBusy(true);
    try {
      await fetch(`/api/agents/autonomy/${enabled ? "enable" : "disable"}`, { method: "POST" });
      loadSup();
    } finally {
      setAutonomyBusy(false);
    }
  };

  // Last Job per agent id, from a single jobs feed (most recent first already).
  const lastJobByAgent = useMemo(() => {
    const map = new Map<string, Job>();
    for (const j of jobs) {
      if (j.agent_id && !map.has(j.agent_id)) map.set(j.agent_id, j);
    }
    return map;
  }, [jobs]);

  if (err) {
    return (
      <div>
        <PageHeader title="Agents" />
        <StateBlock variant="error" title="Couldn't load agents" hint={<code>/api/agents/</code>} />
      </div>
    );
  }
  if (!agents) {
    return <div><PageHeader title="Agents" sub="Loading…" /></div>;
  }

  const total = agents.length;

  return (
    <div>
      <PageHeader
        title="Agents"
        sub={`${total} agent${total === 1 ? "" : "s"}`}
        actions={
          <Row wrap>
            <Link to="/agents/new">
              <Button variant="primary" size="sm"><Plus aria-hidden /> New agent</Button>
            </Link>
            <Button variant="ghost" size="sm" onClick={() => setSpawning(true)}>
              <Plus aria-hidden /> New builder
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setTemplating(true)}>
              <FileStack aria-hidden /> From template
            </Button>
            <Link to="/jobs">
              <Button variant="ghost" size="sm"><ListChecks aria-hidden /> Jobs</Button>
            </Link>
          </Row>
        }
      />

      {sup && (() => {
        // At-a-glance strip: builders live/idle split, assistants count, and the
        // one live autonomy toggle (clears/creates .runtime/AUTONOMY_DISABLED).
        const builders = agents.filter((a) => a.runtime === "builder");
        const assistants = agents.filter((a) => a.runtime === "assistant");
        const liveBuilds = builders.filter((a) => a.state === "running").length;
        const idleBuilds = builders.filter((a) => a.state !== "running").length;
        const autonomyOn = !sup.cron_autonomy.disabled;
        const builderDot: StatusKind = liveBuilds > 0 ? "running" : "ok";
        return (
          <Card style={{ marginBottom: "var(--space-5)", padding: "var(--space-4)" }}>
            <Row between wrap style={{ rowGap: "var(--space-4)" }}>
              <div>
                <SectionLabel>Builders</SectionLabel>
                <Row><StatusDot status={builderDot}>{liveBuilds} live · {idleBuilds} idle</StatusDot></Row>
              </div>
              <div>
                <SectionLabel>Assistants</SectionLabel>
                <Row><StatusDot status="ok">{assistants.length} {assistants.length === 1 ? "assistant" : "assistants"}</StatusDot></Row>
              </div>
              <div>
                <SectionLabel>Autonomy</SectionLabel>
                <Row>
                  <StatusDot status={autonomyOn ? "running" : "stopped"}>{autonomyOn ? "On" : "Off"}</StatusDot>
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

      {total === 0 ? (
        <StateBlock
          title="No agents yet"
          hint={<span>Use <strong>+ New agent</strong> to add an assistant or builder, or <strong>+ New builder</strong> to start a Claude Code session on a project.</span>}
        />
      ) : (
        <CardGrid className="ws-agents-grid">
          {agents.map((a) => {
            const isBuilder = a.runtime === "builder";
            const Icon = isBuilder ? Bot : Brain;
            const when = activityLabel(a.last_active || a.created_at);
            const lastJob = lastJobByAgent.get(a.id);
            return (
              <li key={`${a.runtime}:${a.id}`} className={isBuilder ? "ws-agent-card-cell" : undefined}>
                <Link
                  to={isBuilder
                    ? `/agents/${encodeURIComponent(a.slug)}/chat`
                    : `/agents/${encodeURIComponent(a.id)}`}
                  style={{ textDecoration: "none", color: "inherit" }}
                >
                  <Card interactive className="ws-agent-card">
                    <Row between>
                      <h2 className="ws-card__title">
                        <Icon size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name}
                      </h2>
                      <Tag hue={isBuilder ? "turquoise" : "violet"}>{a.runtime}</Tag>
                    </Row>
                    <p className="ws-card__meta">{a.purpose || (isBuilder ? (a.project_slug ? `linked to ${a.project_slug}` : "freestanding") : "assistant")}</p>
                    {(a.autonomy || a.role || a.depth || a.latitude) && (
                      <Row wrap style={{ gap: "var(--space-1)", marginBottom: "var(--space-1)" }}>
                        {a.autonomy && <Badge>{presetIcon(a.autonomy)}{a.autonomy}</Badge>}
                        {a.role && <Tag hue={catFor(`role:${a.role}`)}>{a.role}</Tag>}
                        {a.depth && (
                          <Badge><Gauge size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{a.depth}</Badge>
                        )}
                        {a.latitude && (
                          <span title={latitudeDesc(a.latitude)}>
                            <Badge>
                              <Compass size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{a.latitude}
                            </Badge>
                          </span>
                        )}
                      </Row>
                    )}
                    <p className="ws-card__meta">{triggerSummary(a.triggers)}</p>
                    <p className="ws-card__meta ws-muted">
                      {lastJob ? (
                        <>last job: {lastJob.status} · {activityLabel(lastJob.started_at)}</>
                      ) : (
                        <>next: {nextScheduled(a.triggers)}</>
                      )}
                    </p>
                    <p className="ws-card__meta ws-agent-card__when">
                      {when && <><Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{when}</>}
                    </p>
                  </Card>
                </Link>
                {isBuilder && (
                  <button
                    type="button"
                    className="ws-agent-card__workbench"
                    onClick={() => nav(`/agents/${encodeURIComponent(a.id)}`)}
                    title="Agent settings (triggers · jobs · config)"
                    aria-label="Builder settings"
                  >
                    <SlidersHorizontal size={16} aria-hidden />
                  </button>
                )}
              </li>
            );
          })}
        </CardGrid>
      )}

      {/* Global autonomy pause + supervisor + Ollama/GPU broker controls. Moved
          here from Settings (Phase 5): the fleet they govern lives on this page,
          and the AUTONOMY_DISABLED switch stays reachable. */}
      <div style={{ marginTop: "var(--space-7)" }}>
        <AutonomyControls />
      </div>

      <NewBuilderModal
        open={spawning}
        projects={projects}
        existing={build || []}
        onCancel={() => setSpawning(false)}
        onCreated={(slug) => { setSpawning(false); nav(`/agents/${encodeURIComponent(slug)}/chat`); }}
        onNewProject={() => { setSpawning(false); nav("/projects/new"); }}
      />

      <FromTemplateModal
        open={templating}
        projects={projects}
        onCancel={() => setTemplating(false)}
        onDone={(slug) => {
          setTemplating(false);
          loadAll();
          if (slug) nav(`/agents/${encodeURIComponent(slug)}`);
        }}
      />
    </div>
  );
}
