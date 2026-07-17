import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { Trash2, Plus, Bot, Brain, MessageSquare, Clock, StickyNote } from "lucide-react";
import type { Project } from "@shared/types";
import {
  ActionBar,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Field,
  Input,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Skeleton,
  StateBlock,
  StatusPill,
  Stack,
  Tag,
  Textarea,
  catFor,
  type SegmentedOption,
} from "../ui";
import { type Job, jobStatusKind } from "../lib/jobs";
import GrantPicker from "../components/GrantPicker";
import TaskPanel from "../components/TaskPanel";
import { useCrumbLabel } from "../components/appNav";
import {
  EVENT_SOURCES,
  triggerSummary,
  type Trigger,
  type TriggerKind,
  type UnifiedAgent,
} from "../lib/agentTriggers";

// One Agent, one config surface. `runtime` is a field, not a separate noun.
// This page is the definition (editable), triggers + limits, the grant set
// (Phase-4 scoped Store tokens, minted here via the broker), and its Job history. The
// builder work pane (files / editor / preview / diff + chat) lives separately at
// /agents/:slug/chat.

type Limits = { max_iterations?: number; budget?: { max_wall_s?: number } };

type AgentDetailData = UnifiedAgent & {
  definition?: string;
};

// A Phase-4 resource-scoped grant minted TO this agent (over a Store).
type ResourceGrant = {
  id: string;
  from: string;
  to: string;
  store_uri: string;
  subtree?: boolean;
  verbs: string[];
  caveats?: Record<string, unknown>;
  created_at?: string;
};

const MAX_GRANTS = 200; // overflow guard for the per-agent grants list

const jobKind = jobStatusKind;

const TRIGGER_KINDS: SegmentedOption<TriggerKind>[] = [
  { value: "manual", label: "Manual" },
  { value: "cron", label: "Cron" },
  { value: "event", label: "Event" },
  { value: "discord", label: "Discord" },
  { value: "loop", label: "Loop" },
];

function fmtTs(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// ---------------------------------------------------------------------------
// Trigger list editor
// ---------------------------------------------------------------------------
function TriggerEditor({
  triggers, onChange,
}: {
  triggers: Trigger[];
  onChange: (next: Trigger[]) => void;
}) {
  const update = (i: number, patch: Partial<Trigger>) => {
    const next = triggers.map((t, idx) => (idx === i ? { ...t, ...patch } : t));
    onChange(next);
  };
  const setKind = (i: number, kind: TriggerKind) => {
    // Reset kind-specific fields to sane defaults when switching kind.
    const base: Trigger = { kind };
    if (kind === "cron") base.cron_expression = triggers[i].cron_expression || "";
    if (kind === "event") base.event_sources = triggers[i].event_sources || [];
    if (kind === "discord") base.channels = triggers[i].channels || [];
    if (kind === "loop") {
      base.max_iterations = triggers[i].max_iterations ?? 20;
      base.budget = triggers[i].budget ?? { max_wall_s: 3000 };
    }
    update(i, base);
  };
  const remove = (i: number) => onChange(triggers.filter((_, idx) => idx !== i));
  const add = () => onChange([...triggers, { kind: "manual" }]);
  const toggleSource = (i: number, s: string) => {
    const cur = triggers[i].event_sources || [];
    update(i, { event_sources: cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s] });
  };

  return (
    <Stack>
      {triggers.length === 0 && (
        <p className="ws-muted">No triggers. This agent runs only on manual invocation.</p>
      )}
      {triggers.map((t, i) => (
        <Card key={i} style={{ padding: "var(--space-3)" }}>
          <Row between wrap>
            <Segmented
              options={TRIGGER_KINDS}
              value={t.kind}
              onChange={(k) => setKind(i, k)}
              ariaLabel="Trigger kind"
            />
            <Button size="sm" variant="danger-ghost" onClick={() => remove(i)}>
              <Trash2 aria-hidden /> Remove
            </Button>
          </Row>
          {t.kind === "cron" && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <Field label="Cron expression" htmlFor={`cron-${i}`}>
                <Input
                  id={`cron-${i}`}
                  className="ws-mono"
                  value={t.cron_expression || ""}
                  onChange={(e) => update(i, { cron_expression: e.target.value })}
                  placeholder="*/20 * * * *"
                />
              </Field>
            </div>
          )}
          {t.kind === "event" && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <SectionLabel>Subscribe to event sources</SectionLabel>
              <Row wrap>
                {EVENT_SOURCES.map((s) => (
                  <Checkbox
                    key={s}
                    checked={(t.event_sources || []).includes(s)}
                    onChange={() => toggleSource(i, s)}
                    label={s}
                  />
                ))}
              </Row>
            </div>
          )}
          {t.kind === "discord" && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <Field label="Channel IDs" htmlFor={`channels-${i}`}>
                <Input
                  id={`channels-${i}`}
                  className="ws-mono"
                  value={(t.channels || []).join(", ")}
                  onChange={(e) =>
                    update(i, {
                      channels: e.target.value
                        .split(/[\s,]+/)
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="327927866052444164"
                />
              </Field>
              <p className="ws-muted" style={{ marginTop: "var(--space-2)" }}>
                Discord channel snowflakes this agent answers. Empty means subscribed to nothing (inert).
              </p>
            </div>
          )}
          {t.kind === "loop" && (
            <Row wrap style={{ marginTop: "var(--space-3)" }}>
              <Field label="Max iterations" htmlFor={`iters-${i}`}>
                <Input
                  id={`iters-${i}`}
                  type="number"
                  min={1}
                  value={t.max_iterations ?? 20}
                  onChange={(e) => update(i, { max_iterations: Number(e.target.value) || 1 })}
                  style={{ width: "8rem" }}
                />
              </Field>
              <Field label="Budget (wall seconds)" htmlFor={`budget-${i}`}>
                <Input
                  id={`budget-${i}`}
                  type="number"
                  min={1}
                  value={t.budget?.max_wall_s ?? 3000}
                  onChange={(e) => update(i, { budget: { max_wall_s: Number(e.target.value) || 1 } })}
                  style={{ width: "8rem" }}
                />
              </Field>
            </Row>
          )}
        </Card>
      ))}
      <Row>
        <Button variant="ghost" size="sm" onClick={add}>
          <Plus aria-hidden /> Add trigger
        </Button>
      </Row>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Create form (runtime picker) - the real /agents/new flow
// ---------------------------------------------------------------------------
type CreateRuntime = "assistant" | "builder";
const CREATE_RUNTIME: SegmentedOption<CreateRuntime>[] = [
  { value: "assistant", label: "Assistant" },
  { value: "builder", label: "Builder" },
];

function CreateAgent() {
  const nav = useNavigate();
  useCrumbLabel("New agent");
  const [runtime, setRuntime] = useState<CreateRuntime>("assistant");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [persona, setPersona] = useState("");
  const [human, setHuman] = useState("");
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectSlug, setProjectSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/projects/")
      .then((r) => (r.ok ? r.json() : []))
      .then((p: Project[]) => {
        setProjects(p);
        if (p.length) setProjectSlug(p[0].slug);
      })
      .catch(() => setProjects([]));
  }, []);

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      if (runtime === "assistant") {
        const r = await fetch("/api/agents/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim(), persona, human }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const a = await r.json();
        nav(`/agents/${encodeURIComponent(a.id)}`);
      } else {
        if (!projectSlug) throw new Error("pick a project for the agent");
        const slug = slugify(name.trim());
        const r = await fetch("/api/agents/build", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slug, name: name.trim(), purpose: purpose.trim(), project_slug: projectSlug }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const a = await r.json();
        nav(`/agents/${encodeURIComponent(a.slug)}/chat`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <PageHeader title="New agent" sub="One Agent concept. Pick a runtime." />
      {err && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock variant="error" title="Couldn't create agent" hint={<code>{err}</code>} />
        </div>
      )}
      <Stack>
        <div>
          <SectionLabel>Runtime</SectionLabel>
          <Segmented options={CREATE_RUNTIME} value={runtime} onChange={setRuntime} ariaLabel="Runtime" />
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginTop: "var(--space-2)" }}>
            {runtime === "assistant"
              ? "An assistant agent with its own instructions and notes."
              : "A Claude Code coding session scoped to a project."}
          </p>
        </div>
        <Field label="Name" required htmlFor="new-name">
          <Input id="new-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        {runtime === "assistant" ? (
          <>
            <Field label="Persona" htmlFor="new-persona">
              <Textarea id="new-persona" className="ws-mono" value={persona} onChange={(e) => setPersona(e.target.value)} rows={8} />
            </Field>
            <Field label="Human (operator context)" htmlFor="new-human">
              <Textarea id="new-human" className="ws-mono" value={human} onChange={(e) => setHuman(e.target.value)} rows={6} />
            </Field>
          </>
        ) : (
          <>
            <Field label="Project" required>
              <Select value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)} disabled={!projects}>
                {!projects && <option value="">Loading…</option>}
                {projects && projects.length === 0 && <option value="">No projects yet</option>}
                {projects?.map((p) => (
                  <option key={p.slug} value={p.slug}>{p.title}</option>
                ))}
              </Select>
            </Field>
            <Field label="Purpose" help="One sentence: what is this agent working on?">
              <Input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. build the landing hero" />
            </Field>
          </>
        )}
        <Row end>
          <Button variant="ghost" onClick={() => nav("/agents")} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={busy || !name.trim()}>
            Create agent
          </Button>
        </Row>
      </Stack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notes (agent:<slug> knowledge scope)
// ---------------------------------------------------------------------------
// The same scoped-notes substrate the Project detail page uses, on the agent
// scope. Operator writes and agent writes share it, and it IS injected into the
// agent's chat context. List via GET /api/knowledge/agent:<slug>/notes, write a
// fresh note via the per-note PUT route (same shape as the /notes surface).
type KnowledgeNote = { id: string; title: string; hook: string; updated?: string | null };

function freshNoteId(title: string): string {
  const base =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "note";
  return `${base}-${Date.now().toString(36)}`;
}

function AgentNotes({ slug }: { slug: string }) {
  const scope = `agent:${slug}`;
  const [notes, setNotes] = useState<KnowledgeNote[] | null>(null);
  const [notesErr, setNotesErr] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [postErr, setPostErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setNotesErr(null);
    try {
      const r = await fetch(`/api/knowledge/${encodeURIComponent(scope)}/notes`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNotes(await r.json());
    } catch (e) {
      setNotesErr(String(e));
      setNotes([]);
    }
  }, [scope]);

  useEffect(() => { load(); }, [load]);

  const post = useCallback(async () => {
    const t = title.trim();
    if (!t && !body.trim()) return;
    setPosting(true);
    setPostErr(null);
    try {
      const id = freshNoteId(t);
      const r = await fetch(
        `/api/knowledge/${encodeURIComponent(scope)}/notes/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: t, content: body, hook: "" }),
        },
      );
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setTitle("");
      setBody("");
      await load();
    } catch (e) {
      setPostErr(String(e));
    } finally {
      setPosting(false);
    }
  }, [title, body, scope, load]);

  return (
    <section>
      <Row between wrap style={{ alignItems: "baseline" }}>
        <SectionLabel icon={<StickyNote aria-hidden />}>Notes</SectionLabel>
        <Link to={`/notes?scope=${encodeURIComponent(scope)}`} className="ws-bc__link">
          View all in Notes
        </Link>
      </Row>
      <Card style={{ padding: "var(--space-3)" }}>
        <Stack>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Note title"
            aria-label="Note title"
          />
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Leave a note for this agent. Notes here are read and written by the agent too, and injected into its chat context."
            rows={3}
          />
          <Row>
            <Button variant="primary" onClick={post} loading={posting} disabled={!title.trim() && !body.trim()}>
              <Plus aria-hidden /> Save note
            </Button>
          </Row>
          {postErr && <p className="ws-field__error">{postErr}</p>}

          {notes === null ? (
            <Stack gap="sm">
              <Skeleton width="65%" />
              <Skeleton width="50%" />
            </Stack>
          ) : notesErr ? (
            <StateBlock
              variant="error"
              title="Couldn't load notes"
              hint={<code>GET /api/knowledge/{scope}/notes ({notesErr})</code>}
              action={<Button size="sm" onClick={load}>Retry</Button>}
            />
          ) : notes.length === 0 ? (
            <StateBlock
              title="No notes yet"
              hint="Save a note above to leave context for this agent. The agent writes notes here too, and everything here is injected into its chat context."
            />
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {notes.map((n) => (
                <li key={n.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                  <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                    <span style={{ fontWeight: 600, wordBreak: "break-word" }}>{n.title || n.id}</span>
                    {n.updated && (
                      <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(n.updated)}</span>
                    )}
                  </Row>
                  {n.hook && (
                    <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-sm)", wordBreak: "break-word" }}>{n.hook}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Stack>
      </Card>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Detail (existing agent)
// ---------------------------------------------------------------------------
function ExistingAgent({ agentId }: { agentId: string }) {
  const nav = useNavigate();
  const [agent, setAgent] = useState<AgentDetailData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [confirmRetire, setConfirmRetire] = useState(false);

  // Phase-4 grants minted to this agent, plus the broker (GrantPicker) mount.
  const [grants, setGrants] = useState<ResourceGrant[] | null>(null);
  const [grantsErr, setGrantsErr] = useState<string | null>(null);
  const [grantPickerOpen, setGrantPickerOpen] = useState(false);

  // Draft (edit-mode). Nested triggers/limits are managed manually so dirty
  // detection is deep, not the shallow useEditMode compare.
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [dName, setDName] = useState("");
  const [dPurpose, setDPurpose] = useState("");
  const [dBody, setDBody] = useState("");
  const [dTriggers, setDTriggers] = useState<Trigger[]>([]);
  const [dLimits, setDLimits] = useState<Limits>({});

  useCrumbLabel(agent?.name || null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const a: AgentDetailData = await r.json();
      setAgent(a);
    } catch (e) {
      setErr(String(e));
    }
  }, [agentId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    fetch(`/api/agents/${encodeURIComponent(agentId)}/jobs?limit=25`)
      .then((r) => (r.ok ? r.json() : { jobs: [] }))
      .then((d) => setJobs(d.jobs || []))
      .catch(() => setJobs([]));
  }, [agentId]);

  const loadGrants = useCallback(async () => {
    setGrantsErr(null);
    setGrants(null);
    try {
      const r = await fetch("/api/grants");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const data = (await r.json()) as { resource_grants?: ResourceGrant[] };
      const to = `agent:${agentId}`;
      setGrants((data.resource_grants || []).filter((g) => g.to === to));
    } catch (e) {
      setGrantsErr(String(e));
    }
  }, [agentId]);

  useEffect(() => { loadGrants(); }, [loadGrants]);

  const personaValue = useMemo(() => agent?.definition ?? "", [agent]);

  const beginEdit = () => {
    if (!agent) return;
    setDName(agent.name || "");
    setDPurpose(agent.purpose || "");
    setDBody(agent.runtime === "assistant" ? personaValue : agent.definition || "");
    setDTriggers((agent.triggers || []).map((t) => ({ ...t })));
    setDLimits(agent.limits || { max_iterations: 20, budget: { max_wall_s: 3000 } });
    setEditing(true);
    setFlash(false);
  };

  const save = async () => {
    if (!agent) return;
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: dName,
          purpose: dPurpose,
          triggers: dTriggers,
          limits: dLimits,
          definition: dBody,
        }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setEditing(false);
      setFlash(true);
      setTimeout(() => setFlash(false), 2000);
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const retire = async () => {
    // Retire is offered for builders (flips build runtime state). Assistant
    // retirement is a later concern; the Retire button is builder-only.
    if (agent?.runtime === "builder") {
      await fetch(`/api/agents/build/${encodeURIComponent(agent.slug)}/retire`, { method: "POST" });
    }
    nav("/agents");
  };

  if (err && !agent) {
    return (
      <div>
        <PageHeader title="Agent" />
        <StateBlock variant="error" title="Couldn't load agent" hint={<code>{err}</code>} />
      </div>
    );
  }
  if (!agent) {
    return <div><PageHeader title="Agent" sub="Loading…" /></div>;
  }

  const isBuilder = agent.runtime === "builder";
  const RuntimeIcon = isBuilder ? Bot : Brain;

  return (
    <div style={{ maxWidth: 820 }}>
      <PageHeader
        title={agent.name}
        sub={
          <span>
            <code className="ws-mono">{agent.slug}</code> ·{" "}
            {agent.project_slug ? <>project <code>{agent.project_slug}</code></> : "user scope"}
          </span>
        }
        actions={
          <Row wrap>
            <Tag hue={isBuilder ? "turquoise" : "violet"}>{agent.runtime}</Tag>
            {isBuilder ? (
              <Button size="sm" variant="primary" onClick={() => nav(`/agents/${encodeURIComponent(agent.slug)}/chat`)}>
                <MessageSquare aria-hidden /> Open work pane
              </Button>
            ) : (
              <Button size="sm" variant="primary" onClick={() => nav(`/chat/${encodeURIComponent(`agent:${agent.slug}`)}`)}>
                <MessageSquare aria-hidden /> Chat
              </Button>
            )}
            {!editing && (
              <Button size="sm" variant="ghost" onClick={beginEdit}>Edit</Button>
            )}
          </Row>
        }
      />

      {err && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock variant="error" title="Save error" hint={<code>{err}</code>} />
        </div>
      )}

      <Stack>
        {/* --- Definition --- */}
        <section>
          <SectionLabel>Definition</SectionLabel>
          {editing ? (
            <Stack>
              <Field label="Name" htmlFor="ad-name">
                <Input id="ad-name" value={dName} onChange={(e) => setDName(e.target.value)} />
              </Field>
              <Field label="Purpose" htmlFor="ad-purpose">
                <Input id="ad-purpose" value={dPurpose} onChange={(e) => setDPurpose(e.target.value)} />
              </Field>
              <Field label={isBuilder ? "Instructions" : "Persona"} htmlFor="ad-body">
                <Textarea id="ad-body" className="ws-mono" value={dBody} onChange={(e) => setDBody(e.target.value)} rows={12} />
              </Field>
            </Stack>
          ) : (
            <Card style={{ padding: "var(--space-3)" }}>
              <p className="ws-card__meta">{agent.purpose || "No purpose set."}</p>
              <p className="ws-muted" style={{ marginTop: "var(--space-2)" }}>
                <RuntimeIcon size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                {isBuilder ? "Claude Code agent" : "Assistant agent"}
                {agent.memory ? ` · memory: ${agent.memory}` : ""}
              </p>
              <pre style={{
                marginTop: "var(--space-3)", padding: "var(--space-3)",
                background: "var(--bg-inset)", borderRadius: "var(--radius-sm)",
                fontSize: "var(--fs-sm)", whiteSpace: "pre-wrap", overflow: "auto", maxHeight: "24vh",
              }}>
                {(agent.runtime === "assistant" ? personaValue : agent.definition) || "(no instructions yet)"}
              </pre>
            </Card>
          )}
        </section>

        {/* --- Triggers --- */}
        <section>
          <SectionLabel>Triggers</SectionLabel>
          {editing ? (
            <TriggerEditor triggers={dTriggers} onChange={setDTriggers} />
          ) : (
            <Card style={{ padding: "var(--space-3)" }}>
              <p className="ws-card__meta">{triggerSummary(agent.triggers)}</p>
            </Card>
          )}
        </section>

        {/* --- Limits --- */}
        <section>
          <SectionLabel>Limits</SectionLabel>
          {editing ? (
            <Row wrap>
              <Field label="Max iterations" htmlFor="ad-iters">
                <Input
                  id="ad-iters"
                  type="number"
                  min={1}
                  value={dLimits.max_iterations ?? 20}
                  onChange={(e) => setDLimits((l) => ({ ...l, max_iterations: Number(e.target.value) || 1 }))}
                  style={{ width: "8rem" }}
                />
              </Field>
              <Field label="Budget (wall seconds)" htmlFor="ad-budget">
                <Input
                  id="ad-budget"
                  type="number"
                  min={1}
                  value={dLimits.budget?.max_wall_s ?? 3000}
                  onChange={(e) => setDLimits((l) => ({ ...l, budget: { max_wall_s: Number(e.target.value) || 1 } }))}
                  style={{ width: "8rem" }}
                />
              </Field>
            </Row>
          ) : (
            <Card style={{ padding: "var(--space-3)" }}>
              <p className="ws-card__meta">
                max {agent.limits?.max_iterations ?? 20} iterations · budget {agent.limits?.budget?.max_wall_s ?? 3000}s
              </p>
            </Card>
          )}
        </section>

        {/* --- Grants (Phase 4: scoped capability tokens over Stores) --- */}
        <section>
          <Row between style={{ alignItems: "center" }}>
            <SectionLabel>Grants</SectionLabel>
            <Button size="sm" onClick={() => setGrantPickerOpen(true)}>
              <Plus aria-hidden /> Grant access to a store
            </Button>
          </Row>
          {grantsErr ? (
            <StateBlock
              variant="error"
              title="Couldn't load grants"
              hint={<code>/api/grants</code>}
              action={<Button onClick={loadGrants}>Retry</Button>}
            />
          ) : grants === null ? (
            <Card style={{ padding: "var(--space-3)" }}><p className="ws-muted">Loading…</p></Card>
          ) : grants.length === 0 ? (
            <StateBlock
              title="No grants"
              hint="Grant this agent scoped access to a Store (read / write / append) through the broker. Minted grants appear here and can be revoked from the Grants settings page."
            />
          ) : grants.length > MAX_GRANTS ? (
            <StateBlock
              title="Too many grants to list safely"
              hint={`${grants.length} grants exceeds the ${MAX_GRANTS}-row cap; manage them from the Grants settings page.`}
            />
          ) : (
            <Stack>
              {grants.map((g) => {
                const caveatKeys = Object.keys(g.caveats || {});
                return (
                  <Card key={g.id} style={{ padding: "var(--space-3)" }}>
                    <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                      <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{g.store_uri}</span>
                      {g.subtree && <Tag hue={catFor("subtree")}>subtree</Tag>}
                    </Row>
                    <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>verbs</span>
                      {g.verbs.length === 0 ? (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>none</span>
                      ) : (
                        g.verbs.map((v) => <Tag key={v} hue={catFor(v)}>{v}</Tag>)
                      )}
                    </Row>
                    {caveatKeys.length > 0 && (
                      <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>caveats</span>
                        {caveatKeys.map((k) => (
                          <StatusPill key={k} status="warn">
                            {k}: {String((g.caveats || {})[k])}
                          </StatusPill>
                        ))}
                      </Row>
                    )}
                  </Card>
                );
              })}
            </Stack>
          )}
        </section>

        {/* --- Tasks (the agent's work queue, canonical cross-scope store) --- */}
        <TaskPanel
          scope={isBuilder ? `build:${agent.slug}` : `agent:${agent.slug}`}
          variant="section"
          heading="Tasks"
        />

        {/* --- Job history --- */}
        <section>
          <SectionLabel>Jobs</SectionLabel>
          {jobs === null ? (
            <Card style={{ padding: "var(--space-3)" }}><p className="ws-muted">Loading…</p></Card>
          ) : jobs.length === 0 ? (
            <StateBlock title="No jobs yet" hint="Bounded executions for this agent appear here (cron ticks, event reactions, loops). Interactive chat turns are not Jobs." />
          ) : (
            <Stack>
              {jobs.map((j) => (
                <Card key={j.id} style={{ padding: "var(--space-3)" }}>
                  <Row between wrap>
                    <Row>
                      <StatusPill status={jobKind(j.status)}>{j.status || "unknown"}</StatusPill>
                      <Tag hue="slate">{j.trigger || "manual"}</Tag>
                    </Row>
                    <span className="ws-card__meta">
                      <Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                      {fmtTs(j.started_at)}
                    </span>
                  </Row>
                </Card>
              ))}
            </Stack>
          )}
        </section>

        {/* --- Notes (agent:<slug> knowledge scope, injected into chat context) --- */}
        <AgentNotes slug={agent.slug} />
      </Stack>

      {editing && (
        <ActionBar flash={flash ? "Saved ✓" : undefined}>
          <Button variant="primary" onClick={save} loading={saving} disabled={!dName.trim()}>Save</Button>
          <Button variant="ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
          {isBuilder && (
            <Button variant="danger-ghost" onClick={() => setConfirmRetire(true)}>
              <Trash2 aria-hidden /> Retire
            </Button>
          )}
        </ActionBar>
      )}

      <GrantPicker
        open={grantPickerOpen}
        onClose={() => setGrantPickerOpen(false)}
        to={`agent:${agent.slug}`}
        onGranted={() => loadGrants()}
      />

      <ConfirmDialog
        open={confirmRetire}
        onCancel={() => setConfirmRetire(false)}
        onConfirm={retire}
        title={`Retire ${agent.name}?`}
        body="Retires the agent so it leaves the roster. The working tree and session are preserved."
        confirmLabel="Retire agent"
      />
    </div>
  );
}

export default function AgentDetail() {
  const { agentId = "" } = useParams();
  if (agentId === "new") return <CreateAgent />;
  return <ExistingAgent agentId={agentId} />;
}
