import { useCallback, useEffect, useMemo, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Bot, Brain, Clock, SlidersHorizontal, ListChecks, Play, RotateCw, Power, Compass, FileStack } from "lucide-react";
import type { ReactNode } from "react";
import type { Project } from "@shared/types";
import {
  Badge,
  Button,
  Card,
  CardGrid,
  Field,
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
  StatusPill,
  Tag,
  catFor,
  type StatusKind,
  Notice,
} from "@workshop/ui";
import DelegatesSection from "../components/DelegatesSection";
import { triggerSummary, nextScheduled, agentAgency, type UnifiedAgent } from "../lib/agentTriggers";
import { latitudeDesc, type Latitude } from "../lib/tasks";
import { type AutonomyMode } from "../lib/autonomy";
import { runHref } from "../lib/jobs";
import { shortHttpError } from "../lib/httpError";

// Roster item enriched with the O6/O7 autonomy fields the backend now returns on
// GET /api/agents/ (autonomy preset + role + latitude). Kept local so the shared
// UnifiedAgent type is untouched; every existing UnifiedAgent field still applies.
type Preset = "off" | "on-demand" | "scheduled" | "continuous";
type RosterAgent = UnifiedAgent & {
  autonomy?: Preset;
  // Per-agent autonomy mode (autonomy-model 2026-07-18). The primary axis;
  // the preset above is the trigger cadence, kept for backward-compat.
  autonomy_mode?: AutonomyMode;
  role?: string | null;
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

type SupervisorStatus = {
  cron_autonomy: { disabled: boolean };
};

type Job = { id: string; agent_id?: string; status?: string; started_at?: string };

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
  scope_defaults?: { autonomy?: string };
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
        if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
                          : "no agents yet"}
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
        {err && <Notice tone="error" detail={err}>Couldn't create from that template.</Notice>}
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
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [sup, setSup] = useState<SupervisorStatus | null>(null);
  const [templating, setTemplating] = useState(false);
  // Scope-kind filter (C3). Persists in URL-less local state; view preference,
  // commits on change per the design system.
  const [scopeFilter, setScopeFilter] = useState<"all" | "workshop" | "project">("all");

  const loadAll = useCallback(async () => {
    try {
      const [aR, pR, jR] = await Promise.all([
        fetch("/api/agents/"),
        fetch("/api/projects/"),
        fetch("/api/jobs?limit=200"),
      ]);
      if (!aR.ok) throw new Error(shortHttpError(aR.status, aR.statusText, await aR.text().catch(() => "")));
      setAgents((await aR.json()) as RosterAgent[]);
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

  useEffect(() => { loadAll(); }, [loadAll]);
  usePoll(loadSup, 15_000);

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
    return (
      <div>
        <PageHeader title="Agents" sub="Loading…" />
        <Stack><Skeleton /><Skeleton /><Skeleton /></Stack>
      </div>
    );
  }

  const total = agents.length;

  return (
    <div>
      <PageHeader
        title="Agents"
        sub={
          <Row wrap style={{ gap: "var(--space-3)" }}>
            <span>{total} agent{total === 1 ? "" : "s"}</span>
            {/* Read-only autonomy status pill (C6). The canonical toggle
                lives on /settings; showing it here so the operator on the
                roster page knows at a glance whether the workshop is
                dispatching or paused. */}
            {sup && (
              <Link to="/settings" style={{ textDecoration: "none" }} title="Manage in Settings">
                <StatusPill status={sup.cron_autonomy.disabled ? "stopped" : "running"}>
                  autonomy {sup.cron_autonomy.disabled ? "off" : "on"}
                </StatusPill>
              </Link>
            )}
          </Row>
        }
        actions={
          <Row wrap>
            <Link to="/agents/new">
              <Button variant="primary" size="sm"><Plus aria-hidden /> New agent</Button>
            </Link>
            <Button variant="ghost" size="sm" onClick={() => setTemplating(true)}>
              <FileStack aria-hidden /> From template
            </Button>
            <Link to="/runs">
              <Button variant="ghost" size="sm"><ListChecks aria-hidden /> Runs</Button>
            </Link>
          </Row>
        }
      />

      {sup && (() => {
        // At-a-glance strip, keyed on ROLE. It used to count `runtime` --
        // "Builders" vs "Assistants" -- but that axis described how an agent
        // executes, not what it is, and it is deleted. The entity model (6.1)
        // types agents by role. Live/idle stays on makers: they hold the long
        // sessions.
        const makers = agents.filter((a) => a.role === "maker");
        const others = agents.filter((a) => a.role !== "maker");
        const liveBuilds = makers.filter((a) => a.state === "running").length;
        const idleBuilds = makers.length - liveBuilds;
        const builderDot: StatusKind = liveBuilds > 0 ? "running" : "ok";
        return (
          <Card style={{ marginBottom: "var(--space-5)", padding: "var(--space-4)" }}>
            <Row between wrap style={{ rowGap: "var(--space-4)" }}>
              <div>
                <SectionLabel>Makers</SectionLabel>
                <Row><StatusDot status={builderDot}>{liveBuilds} live · {idleBuilds} idle</StatusDot></Row>
              </div>
              <div>
                <SectionLabel>Other roles</SectionLabel>
                <Row><StatusDot status="ok">{others.length} {others.length === 1 ? "agent" : "agents"}</StatusDot></Row>
              </div>
            </Row>
          </Card>
        );
      })()}

      {total === 0 ? (
        <StateBlock
          title="No agents yet"
          hint={
            <Stack gap="sm">
              <span>
                Agents are named, typed workers that hold roles like{" "}
                <strong>maker</strong>, <strong>reviewer</strong>,{" "}
                <strong>triager</strong>, or <strong>keeper</strong>. The
                default set for a new workshop is <strong>none</strong>.
              </span>
              <Row wrap style={{ gap: "var(--space-2)" }}>
                <Button size="sm" variant="ghost" onClick={() => setTemplating(true)}>
                  <FileStack aria-hidden /> Adopt a template
                </Button>
                <Link to="/agents/new">
                  <Button size="sm" variant="ghost"><Plus aria-hidden /> Start blank</Button>
                </Link>
              </Row>
            </Stack>
          }
        />
      ) : (
        <>
          {/* Scope-kind filter chips (C3). Split the roster by home scope:
              workshop-scope agents (no project link) and project-scope
              agents (a project_slug). "All" is the default and current
              behavior. */}
          <Row wrap style={{ gap: "var(--space-2)", marginBottom: "var(--space-4)" }}>
            <Segmented
              ariaLabel="Scope filter"
              value={scopeFilter}
              onChange={setScopeFilter}
              options={[
                { value: "all", label: "All" },
                { value: "workshop", label: "Workshop" },
                { value: "project", label: "Projects" },
              ]}
            />
          </Row>
          <CardGrid className="ws-agents-grid">
            {agents.filter((a) => {
              if (scopeFilter === "workshop") return !a.project_slug;
              if (scopeFilter === "project") return !!a.project_slug;
              return true;
            }).map((a) => {
            // A maker WITH a project link is the agent that works in a tree,
            // and it is the only one with a work pane to open. Role alone is not
            // the test: a project-less maker has no tree, so linking it to a
            // build page would open a page for a directory that does not exist.
            const isBuilder = a.role === "maker" && !!a.project_slug;
            const Icon = isBuilder ? Bot : Brain;
            const when = activityLabel(a.last_active || a.created_at);
            const lastJob = lastJobByAgent.get(a.id);
            return (
              <li key={a.id} className={isBuilder ? "ws-agent-card-cell" : undefined}>
                {/* MARK THE EXCEPTION, NOT THE DEFAULT. Only an agent that
                    can start its own work wears a rail; one that runs when
                    you ask is the ordinary case and gets nothing. Striping
                    every card is how a roster ends up looking sorted by
                    color while saying nothing. A failed last run replaces the
                    rail with a danger hairline: broken beats provenance. */}
                <Card
                  interactive
                  className={`ws-agent-card${
                    lastJob?.status === "failed"
                      ? " ws-agency-rail is-failed"
                      : agentAgency(a.triggers) === "autonomous" ? " ws-agency-rail" : ""
                  }`}
                  data-agency={agentAgency(a.triggers)}
                >
                  {/* The card link stops above the last-run line, because that
                      line is a link of its own now and an <a> cannot contain
                      one. It used to wrap the whole Card. */}
                  <Link
                    to={isBuilder
                      ? `/agents/${encodeURIComponent(a.slug)}/chat`
                      : `/agents/${encodeURIComponent(a.id)}`}
                    style={{ display: "block", textDecoration: "none", color: "inherit" }}
                  >
                    <Row between>
                      <h2 className="ws-card__title">
                        <Icon size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name}
                      </h2>
                      {/* Role is the type, and the only one: the `runtime`
                          tag that used to sit here is deleted. An agent may
                          genuinely have no role, and "no role" is the honest
                          label for that. */}
                      {a.role
                        ? <Tag hue={catFor(`role:${a.role}`)}>{a.role}</Tag>
                        : <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>no role</span>}
                    </Row>
                    <p className="ws-card__meta">{a.purpose || (a.project_slug ? `linked to ${a.project_slug}` : "no project link")}</p>
                    {(a.autonomy_mode || a.autonomy || a.latitude) && (
                      <Row wrap style={{ gap: "var(--space-1)", marginBottom: "var(--space-1)" }}>
                        {/* Autonomy mode is the primary axis; if unset, fall
                            back to the legacy preset badge for the transition. */}
                        {a.autonomy_mode
                          ? <Badge>{a.autonomy_mode}</Badge>
                          : a.autonomy && <Badge>{presetIcon(a.autonomy)}{a.autonomy}</Badge>}
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
                  </Link>
                  {/* The run itself, not a description of it. This printed a
                      status and a time and went nowhere, on the one line of
                      the card that names a thing with its own page. */}
                  <p className="ws-card__meta ws-muted">
                    {lastJob ? (
                      <>
                        last run:{" "}
                        <Link className="ws-muted" to={runHref(lastJob.id)}>
                          {lastJob.status} · {activityLabel(lastJob.started_at)}
                        </Link>
                      </>
                    ) : (
                      <>next: {nextScheduled(a.triggers)}</>
                    )}
                  </p>
                  <p className="ws-card__meta ws-agent-card__when">
                    {when && <><Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{when}</>}
                  </p>
                </Card>
                {isBuilder && (
                  <button
                    type="button"
                    className="ws-agent-card__workbench"
                    onClick={() => nav(`/agents/${encodeURIComponent(a.id)}`)}
                    title="Agent settings (triggers · runs · config)"
                    aria-label="Agent settings"
                  >
                    <SlidersHorizontal size={16} aria-hidden />
                  </button>
                )}
              </li>
            );
          })}
          </CardGrid>
        </>
      )}

      {/* Who can be CALLED, as opposed to who runs. The roster above is agents
          this workshop DISPATCHES; delegates only run because something invoked
          them, so they answer the question the roster cannot. */}
      <DelegatesSection />

      {/* Autonomy + GPU/Ollama controls moved back to Settings (C6). The read-
          only status pill in the PageHeader shows the state at a glance and
          links to the canonical toggle in /settings. */}

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
