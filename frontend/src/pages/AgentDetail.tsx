import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { Trash2, Plus, Bot, Brain, MessageSquare, Clock, StickyNote, ListChecks, Activity } from "lucide-react";
import type { Project } from "@shared/types";
import {
  ActionBar,
  Badge,
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
  StatusDot,
  StatusPill,
  Stack,
  Tag,
  Textarea,
  catFor,
  type SegmentedOption,
} from "@workshop/ui";
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
import { ticketHref } from "../lib/tasks";

// One Agent, one config surface. `runtime` is a field, not a separate noun.
// This page is the definition (editable), triggers, the grant set
// (Phase-4 scoped Store tokens, minted here via the broker), and its Job history. The
// builder work pane (files / editor / preview / diff + chat) lives separately at
// /agents/:slug/chat.

type AgentDetailData = UnifiedAgent & {
  definition?: string;
  role?: string | null;
  latitude?: string | null;
  autonomy_mode?: "reactive" | "supervised" | "autonomous";
  mentionable?: boolean;
  delegatable?: boolean;
  linked_ticket_ids?: string[];
  // The one scope every linked ticket lives in; a ticket is addressed by
  // (scope, id), so without this the ids are unlinkable.
  linked_ticket_scope?: string | null;
  linked_run_ids?: string[];
  linked_chat_ids?: string[];
  // Why this agent does or does not run, as an ordered chain (backend
  // _activation_chain). Read-only explanation of existing state: it grants
  // nothing and gates nothing.
  activation?: {
    links: { id: string; label: string; ok: boolean; detail: string }[];
    blocked_by: string | null;
    runs_unattended: boolean;
    ever_ran: boolean;
    last_active?: string | null;
  };
  // What the role permits, derived server-side from the write set so the page
  // cannot describe a grant the run does not have.
  authority?: { writes: string[]; describes: string; tools: string[] };
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
// Create form (role picker) — the real /agents/new flow
// ---------------------------------------------------------------------------
// Post-2026-07-18: agents are keyed by role, not runtime. Runtime is derived:
// role=maker + a linked project produces a `builder` runtime (Claude Code coding
// session); anything else is an `assistant`. Broker is closed to interactive
// creation per primitive-roles §2 (demand-only, no runner needed).

type CreateRole = "none" | "maker" | "reviewer" | "triager" | "keeper";
const CREATE_ROLE: SegmentedOption<CreateRole>[] = [
  { value: "none", label: "None" },
  { value: "maker", label: "Maker" },
  { value: "reviewer", label: "Reviewer" },
  { value: "triager", label: "Triager" },
  { value: "keeper", label: "Keeper" },
];

const ROLE_DESC: Record<CreateRole, string> = {
  none: "A named agent with no gated role. Free-form.",
  maker: "Creates artifacts. With a project link, spawns as a Claude Code coding session (builder).",
  reviewer: "Judges someone else's output. Cannot self-approve.",
  triager: "Routes and classifies inbound work into tickets. Writes no artifacts.",
  keeper: "Maintains one designated store (per-scope memory keeper).",
};

function CreateAgent() {
  const nav = useNavigate();
  useCrumbLabel("New agent");
  const [role, setRole] = useState<CreateRole>("none");
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
      .then((p: Project[]) => setProjects(p))
      .catch(() => setProjects([]));
  }, []);

  // maker + project => Claude Code coding session (builder runtime); everything
  // else => assistant runtime. Kept as a derived value, never a separate control.
  const isBuilderPath = role === "maker" && !!projectSlug;

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      if (isBuilderPath) {
        const slug = slugify(name.trim());
        const r = await fetch("/api/agents/build", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slug, name: name.trim(), purpose: purpose.trim(), project_slug: projectSlug }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const a = await r.json();
        nav(`/agents/${encodeURIComponent(a.slug)}/chat`);
      } else {
        const body: Record<string, unknown> = {
          name: name.trim(),
          persona,
          human,
        };
        if (role !== "none") body.role = role;
        if (projectSlug) body.project_slug = projectSlug;
        const r = await fetch("/api/agents/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const a = await r.json();
        nav(`/agents/${encodeURIComponent(a.id)}`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="New agent" sub="Pick a role. Everything else is defaulted; you can tune later." />
      {err && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock variant="error" title="Couldn't create agent" hint={<code>{err}</code>} />
        </div>
      )}
      <Stack>
        <div>
          <SectionLabel>Role</SectionLabel>
          <Segmented options={CREATE_ROLE} value={role} onChange={setRole} ariaLabel="Role" />
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginTop: "var(--space-2)" }}>
            {ROLE_DESC[role]}
          </p>
        </div>
        <Field label="Name" required htmlFor="new-name">
          <Input id="new-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field
          label="For project"
          help={
            role === "maker"
              ? "A maker linked to a project spawns as a Claude Code coding session (builder)."
              : "Optional. Links this agent to a project scope."
          }
        >
          <Select value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)} disabled={!projects}>
            <option value="">(workshop scope)</option>
            {projects?.map((p) => (
              <option key={p.slug} value={p.slug}>{p.title}</option>
            ))}
          </Select>
        </Field>
        {isBuilderPath ? (
          <Field label="Purpose" help="One sentence: what is this agent working on?">
            <Input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. build the landing hero" />
          </Field>
        ) : (
          <>
            <Field label="Persona" htmlFor="new-persona" help="The prompt / behavior of this agent.">
              <Textarea id="new-persona" className="ws-mono" value={persona} onChange={(e) => setPersona(e.target.value)} rows={8} />
            </Field>
            <Field label="Human (operator context)" htmlFor="new-human" help="What the agent should know about you.">
              <Textarea id="new-human" className="ws-mono" value={human} onChange={(e) => setHuman(e.target.value)} rows={6} />
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
            placeholder="Leave a note for this agent."
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
              hint="Notes here are shared with this agent and go into its chat context."
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

  // Draft (edit-mode). Nested triggers are managed manually so dirty detection
  // is deep, not the shallow useEditMode compare.
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [dName, setDName] = useState("");
  const [dPurpose, setDPurpose] = useState("");
  const [dBody, setDBody] = useState("");
  const [dMentionable, setDMentionable] = useState(false);
  const [dDelegatable, setDDelegatable] = useState(false);
  const [dTriggers, setDTriggers] = useState<Trigger[]>([]);

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

  // P2: start a new chat with this agent as a participant. Chats live in the
  // scope where work happens (project:X for a project-linked agent, workshop
  // otherwise); the agent joins as a participant per the new entity model.
  // We navigate to /chat/<context_key>?thread=<new-id> so FocusedChat resumes
  // the freshly-created thread instead of opening a blank one.
  const startChatWithAgent = async (a: AgentDetailData) => {
    const contextKey = a.project_slug ? `project:${a.project_slug}` : "general";
    try {
      const r = await fetch("/api/chat/threads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          context_key: contextKey,
          title: `Chat with ${a.name || a.slug}`,
          participants: [`agent:${a.slug}`],
        }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const t = await r.json();
      nav(`/chat/${encodeURIComponent(contextKey)}?thread=${encodeURIComponent(t.id)}`);
    } catch (e) {
      // Fall back to the legacy shortcut if the create failed for any reason;
      // FocusedChat will open on the legacy agent:<slug> route rather than
      // silently leaving the operator on AgentDetail.
      nav(`/chat/${encodeURIComponent(`agent:${a.slug}`)}`);
      // The console error is enough here — the operator sees the chat land.
      // eslint-disable-next-line no-console
      console.error("start-chat-with-agent failed:", e);
    }
  };

  const beginEdit = () => {
    if (!agent) return;
    setDName(agent.name || "");
    setDPurpose(agent.purpose || "");
    setDBody(agent.runtime === "assistant" ? personaValue : agent.definition || "");
    setDTriggers((agent.triggers || []).map((t) => ({ ...t })));
    setDMentionable(!!agent.mentionable);
    setDDelegatable(!!agent.delegatable);
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
          mentionable: dMentionable,
          delegatable: dDelegatable,
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
    return (
      <div>
        <PageHeader title="Agent" sub="Loading…" />
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="40%" /></Stack></Card>
      </div>
    );
  }

  const isBuilder = agent.runtime === "builder";
  const RuntimeIcon = isBuilder ? Bot : Brain;

  return (
    <div>
      <PageHeader
        title={agent.name}
        sub={
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            {/* Role + latitude + autonomy_mode as the primary chips (C10).
                catFor gives each role its stable hue. */}
            {agent.role && <Tag hue={catFor(`role:${agent.role}`)}>{agent.role}</Tag>}
            {agent.autonomy_mode && agent.autonomy_mode !== "reactive" && (
              <Tag hue={catFor(agent.autonomy_mode)}>{agent.autonomy_mode}</Tag>
            )}
            {agent.autonomy_mode === "reactive" && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>reactive</span>
            )}
            {agent.latitude && (
              <Tag hue={catFor(`lat:${agent.latitude}`)}>latitude {agent.latitude}</Tag>
            )}
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              <code className="ws-mono">{agent.slug}</code> ·{" "}
              {agent.project_slug ? <>project <code>{agent.project_slug}</code></> : "workshop scope"}
              {" · runs as "}<code>{agent.runtime}</code>
            </span>
          </Row>
        }
        actions={
          <Row wrap>
            {isBuilder ? (
              <Button size="sm" variant="primary" onClick={() => nav(`/agents/${encodeURIComponent(agent.slug)}/chat`)}>
                <MessageSquare aria-hidden /> Open work pane
              </Button>
            ) : (
              <Button size="sm" variant="primary" onClick={() => void startChatWithAgent(agent)}>
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
        {/* --- Role card (C1). One-line description of what this role does,
             so the operator knows the agent's shape at a glance. Full role-
             shaped sub-panels (triager inbox, keeper store, broker verdicts,
             maker in-flight, reviewer queue) live in a future pass; this small
             card is the seed so the roster reads as five distinct kinds
             rather than one uniform "agent". --- */}
        {agent.role && (
          <section>
            <Card compact style={{ padding: "var(--space-3)" }}>
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <Tag hue={catFor(`role:${agent.role}`)}>{agent.role}</Tag>
                <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                  {agent.role === "maker" && "Creates artifacts. Primary producer."}
                  {agent.role === "reviewer" && "Approves or rejects maker output. Cannot self-approve."}
                  {agent.role === "triager" && "Routes and classifies inbound work into tickets."}
                  {agent.role === "keeper" && "Maintains one designated store; runs consolidation."}
                  {agent.role === "broker" && "Mediates cross-store access; never reads the data it adjudicates."}
                </span>
              </Row>
              {/* What the role permits, derived server-side from the write set.
                  This is coordination, not a security boundary: the autonomy
                  loop shares a uid with everything else (SEC-A1).

                  THE TOOL LIST SAYS WHERE IT APPLIES, because it does not apply
                  everywhere. It is passed to the CLI as --allowedTools when this
                  agent is addressed in a chat, and that grant is real. A
                  SCHEDULED run is a different process: the sweep runner invokes
                  `claude -p` with the inherited toolset and one narrowing, the
                  irreversible-action deny list. Printing a bare "Tools: Read
                  Grep Glob" over both was the page describing a restriction that
                  half the runs did not have. Naming the surface is not softened
                  wording; it is the actual extent of the grant. */}
              {agent.authority && (
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline", marginTop: "var(--space-2)" }}>
                  <span style={{ fontSize: "var(--fs-sm)" }}>{agent.authority.describes}</span>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {agent.authority.tools.length
                      ? <>Tools when addressed in a chat: <code className="ws-mono">{agent.authority.tools.join(" ")}</code></>
                      : "No tools when addressed in a chat: it answers from what it is given."}
                  </span>
                </Row>
              )}
              {/* The other surface, stated rather than left to be inferred from
                  the absence of a sentence about it. */}
              {agent.activation?.runs_unattended && (
                <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-2)" }}>
                  On a scheduled run it uses the full toolset, minus the actions
                  that cannot be undone (push, merge, publish, sudo). The role
                  above says what it is for, not what it is prevented from doing.
                </p>
              )}
            </Card>
          </section>
        )}

        {/* --- Activation (P0). Why this agent does or does not run. ---
             An agent is defined by its contract, not discovered by running it,
             so this sits directly under the role and above the definition, and
             it renders whether or not the agent has any history.

             Before this, an agent that had never run showed four empty panels
             and no reason, which reads as a broken page rather than a correctly
             idle one. "Autonomy is on" is a conjunction spread across a global
             brake, the agent's declared mode, and whether anything can start it;
             the chain names the FIRST link that is open. See
             docs/platform-redesign/agent-governance-addendum-2026-08-04.md §5. */}
        {agent.activation && (
          <section>
            <SectionLabel><Activity size={14} aria-hidden /> Activation</SectionLabel>
            <Card compact style={{ padding: "var(--space-3)" }}>
              <Stack gap="sm">
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <StatusPill status={agent.activation.runs_unattended ? "ok" : "stopped"}>
                    {agent.activation.runs_unattended ? "Runs unattended" : "Runs only when you ask"}
                  </StatusPill>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                    {agent.activation.ever_ran
                      ? <>last active {agent.activation.last_active}</>
                      : "has never run"}
                  </span>
                </Row>
                <Stack gap="sm">
                  {agent.activation.links.map((l) => (
                    <Row key={l.id} style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <StatusDot
                        status={l.ok ? "ok" : (agent.activation!.blocked_by === l.id ? "warn" : "stopped")}
                      >
                        {l.label}
                      </StatusDot>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{l.detail}</span>
                    </Row>
                  ))}
                </Stack>
                {/* Named plainly rather than left to be inferred from three dots. */}
                {!agent.activation.runs_unattended && (
                  <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
                    {agent.activation.blocked_by === "brake" &&
                      "The global autonomy brake is engaged, so no agent runs unattended, whatever it declares."}
                    {agent.activation.blocked_by === "autonomy" &&
                      "This agent is reactive: it runs only on manual invocation. Set supervised or autonomous to let its triggers fire."}
                    {agent.activation.blocked_by === "trigger" &&
                      "Nothing can start this agent on its own. Add a cron or event trigger, or invoke it manually."}
                  </p>
                )}
              </Stack>
            </Card>
          </section>
        )}

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
              <Field
                label="Interaction"
                help="Off by default. Most agents are triggered workers that report through their runs."
              >
                <Stack gap="sm">
                  <Checkbox
                    checked={dMentionable}
                    onChange={setDMentionable}
                    label={`Mentionable: can be @${agent.slug}'d in a chat and answers inline`}
                  />
                  <Checkbox
                    checked={dDelegatable}
                    onChange={setDDelegatable}
                    label="Delegatable: can be handed a ticket to work"
                  />
                </Stack>
              </Field>
              <Field label="Definition body" htmlFor="ad-body" help={isBuilder ? "Instructions the Claude Code session runs under." : "Persona / behavior for this agent."}>
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
              <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
                {agent.mentionable
                  ? <Tag hue={catFor("mentionable")}>@-mentionable</Tag>
                  : <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>not mentionable</span>}
                {agent.delegatable
                  ? <Tag hue={catFor("delegatable")}>delegatable</Tag>
                  : <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>not delegatable</span>}
              </Row>
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

        {/* --- Involved in --- */}
        {/* Cross-links denormalized on the /api/agents/{id} response (B1). Each
            list navigates directly into the entity it names. The tests /
            navigability smoke check the operator asked for lands here. */}
        {(agent.linked_chat_ids?.length || agent.linked_ticket_ids?.length || agent.linked_run_ids?.length) ? (
          <section>
            <SectionLabel>Involved in</SectionLabel>
            <Stack gap="sm">
              {agent.linked_chat_ids && agent.linked_chat_ids.length > 0 && (
                <div>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    Chats ({agent.linked_chat_ids.length})
                  </span>
                  <Row wrap style={{ gap: "var(--space-1)", marginTop: "var(--space-1)" }}>
                    {agent.linked_chat_ids.slice(0, 10).map((cid) => (
                      <Link key={cid} to={`/chat/${encodeURIComponent(`agent:${agent.slug}`)}?thread=${encodeURIComponent(cid)}`} style={{ textDecoration: "none" }}>
                        <Badge><MessageSquare size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{cid.slice(0, 8)}</Badge>
                      </Link>
                    ))}
                  </Row>
                </div>
              )}
              {agent.linked_ticket_ids && agent.linked_ticket_ids.length > 0 && (
                <div>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    Tickets ({agent.linked_ticket_ids.length})
                  </span>
                  <Row wrap style={{ gap: "var(--space-1)", marginTop: "var(--space-1)" }}>
                    {agent.linked_ticket_ids.slice(0, 10).map((tid) => (
                      <Link key={tid} to={ticketHref(agent.linked_ticket_scope || "workshop", tid)} style={{ textDecoration: "none" }}>
                        <Badge><ListChecks size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />{tid.slice(0, 8)}</Badge>
                      </Link>
                    ))}
                  </Row>
                </div>
              )}
              {agent.linked_run_ids && agent.linked_run_ids.length > 0 && (
                <div>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    Recent runs ({agent.linked_run_ids.length})
                  </span>
                  <Row wrap style={{ gap: "var(--space-1)", marginTop: "var(--space-1)" }}>
                    {agent.linked_run_ids.slice(0, 10).map((rid) => (
                      <Link key={rid} to={`/runs/${encodeURIComponent(rid)}`} style={{ textDecoration: "none" }}>
                        <Badge>{rid.slice(0, 10)}</Badge>
                      </Link>
                    ))}
                  </Row>
                </div>
              )}
            </Stack>
          </section>
        ) : null}

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

        {/* --- Limits: REMOVED, because nothing read them. ---
             This section offered "Max iterations" and "Budget (wall seconds)"
             as editable numbers, persisted them to the agent's frontmatter, and
             ended there. No executor consulted either one. A sweep's iteration
             cap comes from RALPH_INNER_MAX_ITER in project_ralph.sh, adjusted by
             the TICKET's effort tier; its spend bound is the usage ledger. The
             `limits.budget` value reached only triggers.dispatch(), which has no
             callers at all. The operator could set these dials to anything and
             change nothing about how long an agent ran or what it cost.
             Honoring them instead would mean inventing a per-agent budget path
             that does not exist and that the per-ticket effort tier already
             covers, so the control is gone rather than decorated. The real
             bounds are shown where they actually apply. */}

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
              hint="Give this agent read, write or append access to a store. Grants show up here once made."
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
