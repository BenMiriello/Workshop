import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Bot,
  GitBranch,
  GitMerge,
  Trash2,
  FileText,
  ListTodo,
  StickyNote,
  Rocket,
  Plus,
  Pencil,
  RefreshCw,
  Send,
  ExternalLink,
  Upload,
  Store,
} from "lucide-react";
import type { BuildAgent, Project } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  ConfirmDialog,
  Modal,
  PageHeader,
  Row,
  Stack,
  SectionLabel,
  StateBlock,
  StatusPill,
  StatusDot,
  Tag,
  Toggle,
  Skeleton,
  Textarea,
  Input,
  Select,
  Field,
  ActionBar,
  catFor,
  type StatusKind,
} from "../ui";
import { Badge } from "../ui";
import { useEditMode } from "../ui";
import { useCrumbLabel } from "../components/appNav";
import { marketplaceApi, type RegistryItem } from "../lib/marketplace";

// Project detail = the project's hub (the "workbench"). NOT the coding UI — that
// lives under Agents (AgentBuild). Phase 2 (docs/workshop-v1/02 §1-4) retires the
// opaque "working brain" label; the middle column is three named sections:
//   Brief (PROJECT.md, rendered + Edit mode) · Tasks (structured tree) · Notes.
// Plus Build agents, Branches (main trunk + side branches), and Deployments.

type Worktree = {
  branch_slug: string;
  branch: string;
  dir: string;
};

type TaskStatus = "todo" | "doing" | "done" | "blocked";

type Task = {
  id: string;
  text: string;
  status: TaskStatus;
  owner: string;
  parent: string | null;
  created?: string;
  updated?: string;
};

type NoteEntry = { ts: string | null; who: string; text: string };

const stateKind = (s?: string): StatusKind => {
  switch (s) {
    case "wip": return "running";
    case "sketch": return "warn";
    case "mounted":
    case "shared":
    case "published": return "ok";
    case "parked":
    case "retired": return "stopped";
    default: return "warn";
  }
};

const TASK_STATUS_KIND: Record<TaskStatus, StatusKind> = {
  todo: "stopped",
  doing: "running",
  done: "ok",
  blocked: "danger",
};

const TASK_STATUSES: TaskStatus[] = ["todo", "doing", "done", "blocked"];

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// ---- Tiny, safe Brief renderer ---------------------------------------------
// PROJECT.md is plain text the operator + agents read. Render the common
// markdown shapes (headings, bullets, bold, paragraphs) as real elements — no
// dangerouslySetInnerHTML, no inline hex, tokens only. Good enough for a brief;
// not a full markdown engine.
function renderInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      parts.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    } else {
      parts.push(<code key={key++} className="ws-mono">{tok.slice(1, -1)}</code>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function Brief({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(
        <p key={key++} style={{ margin: "0 0 var(--space-3)", lineHeight: "var(--lh-base)" }}>
          {renderInline(para.join(" "))}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={key++} style={{ margin: "0 0 var(--space-3)", paddingLeft: "var(--space-5)" }}>
          {list.map((li, i) => (
            <li key={i} style={{ marginBottom: "var(--space-1)", lineHeight: "var(--lh-base)" }}>
              {renderInline(li)}
            </li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      const level = Math.min(heading[1].length, 3);
      const fs = level === 1 ? "var(--fs-lg)" : level === 2 ? "var(--fs-md)" : "var(--fs-sm)";
      blocks.push(
        <div
          key={key++}
          style={{
            fontSize: fs,
            fontWeight: "var(--fw-semi)",
            margin: "var(--space-3) 0 var(--space-2)",
            letterSpacing: "-0.01em",
          }}
        >
          {renderInline(heading[2])}
        </div>,
      );
    } else if (bullet) {
      flushPara();
      list.push(bullet[1]);
    } else if (line === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return <div style={{ fontSize: "var(--fs-md)" }}>{blocks}</div>;
}

// ---- Notes parsing ---------------------------------------------------------
// INTERVENTIONS.md is append-only stamped lines: "[<iso>] <who>: <text>".
function parseNotes(content: string): NoteEntry[] {
  return content
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const m = /^\[([^\]]+)\]\s*([^:]+):\s*(.*)$/.exec(line);
      if (m) return { ts: m[1], who: m[2].trim(), text: m[3] };
      return { ts: null, who: "operator", text: line };
    });
}

function fmtTs(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function SpawnAgentModal({
  open,
  onCancel,
  projectSlug,
  projectTitle,
  onCreated,
}: {
  open: boolean;
  onCancel: () => void;
  projectSlug: string;
  projectTitle: string;
  onCreated: (slug: string) => void;
}) {
  const [name, setName] = useState(`${projectTitle} builder`);
  const [purpose, setPurpose] = useState("");
  const [slug, setSlug] = useState(`${projectSlug}-builder`);
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

  return (
    <Modal open={open} onClose={onCancel} title="Spawn Build agent">
      <Stack>
        <p className="ws-muted">
          A Build agent opens a Claude Code coding session scoped to this project's directory.
        </p>
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
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
        {err && <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{err}</p>}
        <Row end>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy || !effectiveSlug || !name.trim()} loading={busy}>
            Spawn
          </Button>
        </Row>
      </Stack>
    </Modal>
  );
}

// ---- Tasks section ----------------------------------------------------------
function TaskRow({
  task,
  childrenOf,
  depth,
  onStatus,
  onDelete,
}: {
  task: Task;
  childrenOf: (parent: string | null, owner: string) => Task[];
  depth: number;
  onStatus: (id: string, status: TaskStatus) => void;
  onDelete: (task: Task) => void;
}) {
  const kids = childrenOf(task.id, task.owner);
  return (
    <li>
      <Row wrap style={{ paddingLeft: `calc(${depth} * var(--space-5))`, paddingTop: "var(--space-2)", paddingBottom: "var(--space-2)", borderBottom: "1px solid var(--border)" }}>
        <Select
          value={task.status}
          onChange={(e) => onStatus(task.id, e.target.value as TaskStatus)}
          aria-label="Task status"
          style={{ width: "9rem" }}
        >
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
        <StatusDot status={TASK_STATUS_KIND[task.status]}>{task.status}</StatusDot>
        <span
          style={{
            flex: "1 1 16rem",
            textDecoration: task.status === "done" ? "line-through" : "none",
            color: task.status === "done" ? "var(--muted)" : "inherit",
          }}
        >
          {task.text}
        </span>
        <Button size="sm" variant="danger-ghost" icon aria-label="Delete task" onClick={() => onDelete(task)}>
          <Trash2 aria-hidden />
        </Button>
      </Row>
      {kids.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {kids.map((k) => (
            <TaskRow key={k.id} task={k} childrenOf={childrenOf} depth={depth + 1} onStatus={onStatus} onDelete={onDelete} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function ProjectDetail() {
  const { slug = "" } = useParams();
  const nav = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Brief (PROJECT.md)
  const [brief, setBrief] = useState("");
  const briefEdit = useEditMode({ content: brief }, async (draft) => {
    await fetch(`/api/projects/${slug}/brief`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: draft.content }),
    });
    setBrief(draft.content);
  });

  // Tasks
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [tasksErr, setTasksErr] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [addText, setAddText] = useState("");
  const [adding, setAdding] = useState(false);
  const [deleteTask, setDeleteTask] = useState<Task | null>(null);
  const [deletingTask, setDeletingTask] = useState(false);

  // Notes (INTERVENTIONS.md)
  const [notes, setNotes] = useState<NoteEntry[] | null>(null);
  const [notesErr, setNotesErr] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [postingNote, setPostingNote] = useState(false);
  const [notePostErr, setNotePostErr] = useState<string | null>(null);

  const [agents, setAgents] = useState<BuildAgent[] | null>(null);
  const [spawning, setSpawning] = useState(false);

  const [worktrees, setWorktrees] = useState<Worktree[] | null>(null);
  const [worktreesErr, setWorktreesErr] = useState<string | null>(null);
  const [worktreesLoading, setWorktreesLoading] = useState(true);
  const [mergeTarget, setMergeTarget] = useState<Worktree | null>(null);
  const [mergeMessage, setMergeMessage] = useState("");
  const [merging, setMerging] = useState(false);
  const [discardTarget, setDiscardTarget] = useState<Worktree | null>(null);
  const [discarding, setDiscarding] = useState(false);

  // Publish-to-marketplace (the Deployments "Public" row).
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishErr, setPublishErr] = useState<string | null>(null);
  const [published, setPublished] = useState<RegistryItem | null>(null);

  useCrumbLabel(project?.title || null);

  // ---- Loaders --------------------------------------------------------------
  const loadWorktrees = useCallback(async () => {
    setWorktreesLoading(true);
    setWorktreesErr(null);
    try {
      const r = await fetch(`/api/worktrees/${slug}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setWorktrees(await r.json());
    } catch (e) {
      setWorktreesErr(String(e));
      setWorktrees([]);
    } finally {
      setWorktreesLoading(false);
    }
  }, [slug]);

  const loadTasks = useCallback(async () => {
    setTasksErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/tasks?include_done=true`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const d = await r.json();
      setTasks(d.tasks as Task[]);
    } catch (e) {
      setTasksErr(String(e));
      setTasks([]);
    }
  }, [slug]);

  const loadNotes = useCallback(async () => {
    setNotesErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/interventions`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const d = await r.json();
      setNotes(parseNotes(d.content || ""));
    } catch (e) {
      setNotesErr(String(e));
      setNotes([]);
    }
  }, [slug]);

  const loadAll = useCallback(async () => {
    try {
      const [pR, bR, aR] = await Promise.all([
        fetch(`/api/projects/${slug}`),
        fetch(`/api/projects/${slug}/brief`),
        fetch(`/api/agents/build`),
      ]);
      if (!pR.ok) throw new Error(`project: ${pR.status} ${await pR.text()}`);
      setProject(await pR.json());
      setBrief((bR.ok ? await bR.json() : { content: "" }).content || "");
      const all: BuildAgent[] = aR.ok ? await aR.json() : [];
      setAgents(all.filter((a) => a.project_slug === slug));
    } catch (e) {
      setErr(String(e));
    }
  }, [slug]);

  useEffect(() => { loadWorktrees(); }, [loadWorktrees]);
  useEffect(() => { loadTasks(); }, [loadTasks]);
  useEffect(() => { loadNotes(); }, [loadNotes]);
  useEffect(() => { loadAll(); }, [loadAll]);

  // ---- Task mutations -------------------------------------------------------
  const addTask = useCallback(async () => {
    const text = addText.trim();
    if (!text) return;
    setAdding(true);
    try {
      await fetch(`/api/projects/${slug}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, owner: "operator" }),
      });
      setAddText("");
      await loadTasks();
    } finally {
      setAdding(false);
    }
  }, [addText, slug, loadTasks]);

  const setTaskStatus = useCallback(async (id: string, status: TaskStatus) => {
    setTasks((prev) => prev?.map((t) => (t.id === id ? { ...t, status } : t)) ?? prev);
    await fetch(`/api/projects/${slug}/tasks/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    await loadTasks();
  }, [slug, loadTasks]);

  const doDeleteTask = useCallback(async () => {
    if (!deleteTask) return;
    setDeletingTask(true);
    try {
      await fetch(`/api/projects/${slug}/tasks/${deleteTask.id}`, { method: "DELETE" });
      await loadTasks();
    } finally {
      setDeletingTask(false);
      setDeleteTask(null);
    }
  }, [deleteTask, slug, loadTasks]);

  // ---- Notes mutation -------------------------------------------------------
  const postNote = useCallback(async () => {
    const text = noteDraft.trim();
    if (!text) return;
    setPostingNote(true);
    setNotePostErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/interventions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNoteDraft("");
      await loadNotes();
    } catch (e) {
      setNotePostErr(String(e));
    } finally {
      setPostingNote(false);
    }
  }, [noteDraft, slug, loadNotes]);

  // ---- Branch mutations -----------------------------------------------------
  const doMerge = useCallback(async () => {
    if (!mergeTarget) return;
    setMerging(true);
    try {
      await fetch(`/api/worktrees/${slug}/${mergeTarget.branch_slug}/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: mergeMessage || `Merge branch ${mergeTarget.branch_slug}` }),
      });
      await loadWorktrees();
    } finally {
      setMerging(false);
      setMergeTarget(null);
      setMergeMessage("");
    }
  }, [mergeTarget, mergeMessage, slug, loadWorktrees]);

  const doDiscard = useCallback(async () => {
    if (!discardTarget) return;
    setDiscarding(true);
    try {
      await fetch(`/api/worktrees/${slug}/${discardTarget.branch_slug}`, { method: "DELETE" });
      await loadWorktrees();
    } finally {
      setDiscarding(false);
      setDiscardTarget(null);
    }
  }, [discardTarget, slug, loadWorktrees]);

  // ---- Publish to marketplace -----------------------------------------------
  // Publishing cuts a local release of this project and lists it in the
  // marketplace, where anyone on this workshop can install (fork) it. Gated by a
  // ConfirmDialog because it exposes the project to the local registry.
  const doPublish = useCallback(async () => {
    setPublishing(true);
    setPublishErr(null);
    try {
      const item = await marketplaceApi.publish({ slug });
      setPublished(item);
      setConfirmPublish(false);
    } catch (e) {
      setPublishErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPublishing(false);
    }
  }, [slug]);

  // ---- Task tree shaping ----------------------------------------------------
  // Group by owner (operator first, then each agent), nest by parent, hide done
  // unless toggled. Build the tree client-side from the flat list.
  const visibleTasks = useMemo(() => {
    if (!tasks) return [];
    return showDone ? tasks : tasks.filter((t) => t.status !== "done");
  }, [tasks, showDone]);

  const ownerOrder = useMemo(() => {
    const owners = new Set<string>(visibleTasks.map((t) => t.owner));
    const agentSlugs = (agents ?? []).map((a) => a.slug);
    const ordered: string[] = [];
    if (owners.has("operator")) ordered.push("operator");
    for (const a of agentSlugs) if (owners.has(a) && !ordered.includes(a)) ordered.push(a);
    for (const o of owners) if (!ordered.includes(o)) ordered.push(o);
    return ordered;
  }, [visibleTasks, agents]);

  const childrenOf = useCallback(
    (parent: string | null, owner: string) =>
      visibleTasks.filter((t) => t.owner === owner && (t.parent ?? null) === parent),
    [visibleTasks],
  );

  const agentName = useCallback(
    (owner: string) => (agents ?? []).find((a) => a.slug === owner)?.name ?? owner,
    [agents],
  );

  const doneCount = useMemo(() => (tasks ?? []).filter((t) => t.status === "done").length, [tasks]);

  if (err) {
    return (
      <div>
        <PageHeader title="Project" />
        <StateBlock variant="error" title="Couldn't load project" hint={<code>{err}</code>} />
      </div>
    );
  }
  if (!project) {
    return <div><PageHeader title="Loading project…" /></div>;
  }

  const sideBranches = (worktrees ?? []).filter((w) => w.branch_slug !== "main" && w.branch !== "main");

  return (
    <div>
      <PageHeader
        title={project.title}
        sub={<span><code className="ws-mono">{project.slug}</code> · {project.purpose}</span>}
        actions={
          <Row>
            <StatusPill status={stateKind(project.state)}>{project.state}</StatusPill>
            <Button size="sm" variant="ghost" onClick={() => { loadAll(); loadTasks(); loadNotes(); loadWorktrees(); }}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </Row>
        }
      />

      {/* --- Build agents on this project --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Bot aria-hidden />}>Build agents</SectionLabel>
          <Button variant="primary" size="sm" onClick={() => setSpawning(true)}>
            <Plus aria-hidden /> Spawn build agent
          </Button>
        </Row>
        {!agents ? (
          <Card><Skeleton width="40%" /></Card>
        ) : agents.length === 0 ? (
          <StateBlock
            title="No build agents yet"
            hint="Spawn one to open a coding session against this project."
          />
        ) : (
          <CardGrid>
            {agents.map((a) => (
              <li key={a.slug}>
                <Link to={`/agents/build/${a.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card interactive>
                    <Row between>
                      <h3 className="ws-card__title">
                        <Bot size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name}
                      </h3>
                      <StatusPill status={a.state === "running" ? "running" : "ok"}>{a.state}</StatusPill>
                    </Row>
                    <p className="ws-card__meta ws-mono">{a.slug}</p>
                    {a.purpose && <p style={{ marginTop: "var(--space-2)" }}>{a.purpose}</p>}
                  </Card>
                </Link>
              </li>
            ))}
          </CardGrid>
        )}
      </section>

      {/* --- Brief (PROJECT.md) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<FileText aria-hidden />}>Brief</SectionLabel>
          {!briefEdit.editing && brief.trim() !== "" && (
            <Button size="sm" variant="ghost" onClick={briefEdit.begin}>
              <Pencil aria-hidden /> Edit
            </Button>
          )}
        </Row>
        <Card>
          {briefEdit.editing ? (
            <Stack>
              <span className="ws-card__meta">PROJECT.md · build agents read this every turn</span>
              <Textarea
                value={briefEdit.draft.content}
                onChange={(e) => briefEdit.set("content", e.target.value)}
                rows={14}
                autoFocus
                className="ws-mono"
                style={{ fontSize: "var(--fs-sm)" }}
              />
              <ActionBar flash={briefEdit.flash ? "Saved" : undefined}>
                <Button variant="ghost" onClick={briefEdit.cancel} disabled={briefEdit.saving}>Cancel</Button>
                <Button variant="primary" onClick={briefEdit.save} loading={briefEdit.saving} disabled={!briefEdit.dirty}>
                  Save
                </Button>
              </ActionBar>
            </Stack>
          ) : brief.trim() === "" ? (
            <StateBlock
              title="Describe what this project is for"
              hint="The brief (PROJECT.md) is the goal + constraints your build agents read every turn."
              action={
                <Button variant="primary" size="sm" onClick={briefEdit.begin}>
                  <Pencil aria-hidden /> Write the brief
                </Button>
              }
            />
          ) : (
            <Brief source={brief} />
          )}
        </Card>
      </section>

      {/* --- Tasks (structured tree) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<ListTodo aria-hidden />}>Tasks</SectionLabel>
          <Toggle
            checked={showDone}
            onChange={setShowDone}
            label={`Show done${doneCount ? ` (${doneCount})` : ""}`}
          />
        </Row>
        <Card>
          <Stack>
            <Row wrap>
              <Input
                value={addText}
                onChange={(e) => setAddText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTask(); } }}
                placeholder="Add a task (operator)…"
                style={{ flex: "1 1 16rem" }}
              />
              <Button variant="primary" onClick={addTask} loading={adding} disabled={!addText.trim()}>
                <Plus aria-hidden /> Add task
              </Button>
            </Row>

            {tasks === null ? (
              <Stack gap="sm">
                <Skeleton width="70%" />
                <Skeleton width="55%" />
                <Skeleton width="60%" />
              </Stack>
            ) : tasksErr ? (
              <StateBlock
                variant="error"
                title="Couldn't load tasks"
                hint={<code>GET /api/projects/{slug}/tasks — {tasksErr}</code>}
                action={<Button size="sm" onClick={loadTasks}>Retry</Button>}
              />
            ) : ownerOrder.length === 0 ? (
              <StateBlock
                title={tasks.length === 0 ? "No tasks yet" : "No open tasks"}
                hint={tasks.length === 0
                  ? "Add a task above, or a build agent can create its own."
                  : "All tasks are done. Toggle “Show done” to see them."}
              />
            ) : visibleTasks.length > 400 ? (
              <StateBlock title="Too many tasks to render" hint="Close out or delete completed tasks to keep this list readable." />
            ) : (
              <Stack>
                {ownerOrder.map((owner) => {
                  const roots = childrenOf(null, owner);
                  if (roots.length === 0) return null;
                  return (
                    <div key={owner}>
                      <Row style={{ marginBottom: "var(--space-1)" }}>
                        {owner === "operator" ? (
                          <Tag hue="slate">operator</Tag>
                        ) : (
                          <Tag hue={catFor(owner)}>{agentName(owner)}</Tag>
                        )}
                      </Row>
                      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                        {roots.map((t) => (
                          <TaskRow
                            key={t.id}
                            task={t}
                            childrenOf={childrenOf}
                            depth={0}
                            onStatus={setTaskStatus}
                            onDelete={setDeleteTask}
                          />
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </Stack>
            )}
          </Stack>
        </Card>
      </section>

      {/* --- Notes (was Interventions) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<StickyNote aria-hidden />}>Notes</SectionLabel>
        <Card>
          <Stack>
            <Row wrap>
              <Input
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); postNote(); } }}
                placeholder="Leave a note for this project…"
                style={{ flex: "1 1 16rem" }}
              />
              <Button variant="primary" onClick={postNote} loading={postingNote} disabled={!noteDraft.trim()}>
                <Send aria-hidden /> Post
              </Button>
            </Row>
            {notePostErr && <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{notePostErr}</p>}

            {notes === null ? (
              <Stack gap="sm">
                <Skeleton width="65%" />
                <Skeleton width="50%" />
              </Stack>
            ) : notesErr ? (
              <StateBlock
                variant="error"
                title="Couldn't load notes"
                hint={<code>GET /api/projects/{slug}/interventions — {notesErr}</code>}
                action={<Button size="sm" onClick={loadNotes}>Retry</Button>}
              />
            ) : notes.length === 0 ? (
              <StateBlock
                title="No notes yet"
                hint="Post a note above to leave context for this project and its agents."
              />
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {notes.slice().reverse().map((n, i) => (
                  <li key={i} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(n.ts)}</span>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{n.who}</span>
                    </Row>
                    <p style={{ margin: "var(--space-1) 0 0", lineHeight: "var(--lh-base)" }}>{n.text}</p>
                  </li>
                ))}
              </ul>
            )}
          </Stack>
        </Card>
      </section>

      {/* --- Branches (git worktrees): main trunk + side branches --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<GitBranch aria-hidden />}>Branches</SectionLabel>
          <Button size="sm" variant="ghost" onClick={loadWorktrees} disabled={worktreesLoading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </Row>
        <Card>
          {/* Trunk is always present: you are working on main. */}
          <Row wrap style={{ paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
            <GitBranch size={16} aria-hidden />
            <span className="ws-mono" style={{ fontWeight: "var(--fw-semi)" }}>main</span>
            <StatusDot status="ok">trunk</StatusDot>
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>Working on main</span>
          </Row>

          {worktreesLoading && !worktrees ? (
            <Stack gap="sm" style={{ paddingTop: "var(--space-3)" }}>
              <Skeleton width="60%" />
              <Skeleton width="45%" />
            </Stack>
          ) : worktreesErr ? (
            <div style={{ paddingTop: "var(--space-3)" }}>
              <StateBlock
                variant="error"
                title="Couldn't load branches"
                hint={<code>GET /api/worktrees/{slug} — {worktreesErr}</code>}
                action={<Button size="sm" onClick={loadWorktrees}>Retry</Button>}
              />
            </div>
          ) : sideBranches.length === 0 ? (
            <p className="ws-muted" style={{ margin: "var(--space-3) 0 0", fontSize: "var(--fs-sm)" }}>
              No side branches; working on main.
            </p>
          ) : sideBranches.length > 50 ? (
            <div style={{ paddingTop: "var(--space-3)" }}>
              <StateBlock title="Too many branches" hint="Merge or discard old ones to keep this list readable." />
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {sideBranches.map((w) => (
                <li key={w.branch_slug} style={{ borderBottom: "1px solid var(--border)", padding: "var(--space-3) 0" }}>
                  <Row wrap style={{ alignItems: "center" }}>
                    <GitBranch size={14} aria-hidden />
                    <span className="ws-mono" style={{ fontSize: "var(--fs-sm)", flex: "1 1 12rem" }}>{w.branch_slug}</span>
                    {/* Condensed, aligned, equal-height actions menu. */}
                    <Row style={{ gap: "var(--space-2)" }}>
                      <Link
                        to={`/projects/${slug}/diff?branch=${encodeURIComponent(w.branch_slug)}`}
                        style={{ textDecoration: "none" }}
                      >
                        <Button size="sm" variant="ghost">
                          <ExternalLink aria-hidden /> Diff
                        </Button>
                      </Link>
                      <Button size="sm" variant="default" onClick={() => { setMergeTarget(w); setMergeMessage(""); }}>
                        <GitMerge aria-hidden /> Merge
                      </Button>
                      <Button size="sm" variant="danger-ghost" icon aria-label="Discard branch" onClick={() => setDiscardTarget(w)}>
                        <Trash2 aria-hidden />
                      </Button>
                    </Row>
                  </Row>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* --- Deployments (was Surfaces) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Rocket aria-hidden />}>Deployments</SectionLabel>
        <Card>
          <Stack>
            <DeploymentRow
              name="Integrated"
              desc="A route mounted inside Workshop."
              active={project.surfaces.integrated != null}
              value={project.surfaces.integrated}
              activeAction={
                <Link to={project.surfaces.integrated || "#"} style={{ textDecoration: "none" }}>
                  <Button size="sm" variant="ghost"><ExternalLink aria-hidden /> Open</Button>
                </Link>
              }
              idleLabel="Mount"
            />
            <DeploymentRow
              name="Standalone"
              desc="Its own origin and port."
              active={project.surfaces.standalone != null}
              value={project.surfaces.standalone ? `:${project.surfaces.standalone.port}` : null}
              activeAction={
                <Button size="sm" variant="ghost" disabled title="Standalone open lands with the runner (Phase 4)">
                  <ExternalLink aria-hidden /> Open
                </Button>
              }
              idleLabel="Start"
            />
            <DeploymentRow
              name="Public"
              desc="Shared / marketplace listing."
              active={project.surfaces.public != null}
              value={project.surfaces.public ?? (published ? `v${published.version}` : null)}
              activeAction={
                <Link to={project.surfaces.public || "#"} style={{ textDecoration: "none" }}>
                  <Button size="sm" variant="ghost"><ExternalLink aria-hidden /> Open</Button>
                </Link>
              }
              idleLabel="Publish"
              // Publishing IS wired (the local marketplace registry). Once
              // published, show the release version + a link to the listing;
              // until then, a real Publish button gated by a confirm dialog.
              idleAction={
                published ? (
                  <Row style={{ gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
                    <Badge>v{published.version}</Badge>
                    <Link to="/marketplace" style={{ textDecoration: "none" }}>
                      <Button size="sm" variant="ghost"><Store aria-hidden /> View in marketplace</Button>
                    </Link>
                  </Row>
                ) : (
                  <Button size="sm" variant="primary" onClick={() => { setPublishErr(null); setConfirmPublish(true); }}>
                    <Upload aria-hidden /> Publish
                  </Button>
                )
              }
            />
          </Stack>
          {publishErr && (
            <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: "var(--space-2) 0 0" }}>
              POST /api/base-apps/registry/publish: {publishErr}
            </p>
          )}
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-3)" }}>
            Publish cuts a local release and lists it in the marketplace. One-click mount + guest tokens land with the deployment runner (app-platform manifests + grants, Phase 4).
          </p>
        </Card>
      </section>

      <SpawnAgentModal
        open={spawning}
        onCancel={() => setSpawning(false)}
        projectSlug={project.slug}
        projectTitle={project.title}
        onCreated={(agentSlug) => {
          setSpawning(false);
          nav(`/agents/build/${agentSlug}`);
        }}
      />

      <Modal
        open={mergeTarget !== null}
        onClose={() => setMergeTarget(null)}
        title={mergeTarget ? `Merge branch ${mergeTarget.branch_slug}` : "Merge branch"}
        actions={
          <>
            <Button onClick={() => setMergeTarget(null)} disabled={merging}>Cancel</Button>
            <Button variant="primary" onClick={doMerge} loading={merging}>Merge</Button>
          </>
        }
      >
        <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
          Merges this branch into main, then deletes the worktree and the
          branch ref. Fails noisily if the merge isn't fast-forward or clean.
        </p>
        <Field label="Merge message" help="Used as the merge-commit message if not fast-forward.">
          <Input
            value={mergeMessage}
            onChange={(e) => setMergeMessage(e.target.value)}
            placeholder={mergeTarget ? `Merge branch ${mergeTarget.branch_slug}` : ""}
            autoFocus
          />
        </Field>
      </Modal>

      <ConfirmDialog
        open={discardTarget !== null}
        onCancel={() => setDiscardTarget(null)}
        onConfirm={doDiscard}
        title="Discard branch?"
        body={
          <span>
            Removes the worktree and deletes the branch <code className="ws-mono">{discardTarget?.branch}</code>.
            Any uncommitted work in the worktree is lost. This can't be undone.
          </span>
        }
        confirmLabel="Discard"
        busy={discarding}
      />

      <ConfirmDialog
        open={deleteTask !== null}
        onCancel={() => setDeleteTask(null)}
        onConfirm={doDeleteTask}
        title="Delete task?"
        body={
          <span>
            Permanently removes the task <strong>{deleteTask?.text}</strong> and any of its sub-tasks.
            This can't be undone.
          </span>
        }
        confirmLabel="Delete"
        busy={deletingTask}
      />

      <ConfirmDialog
        open={confirmPublish}
        onCancel={() => setConfirmPublish(false)}
        onConfirm={doPublish}
        title="Publish to the marketplace?"
        body={
          <span>
            Cuts a local release of <strong>{project.title}</strong> and lists it in the
            marketplace, where anyone on this workshop can install (fork) it. This
            exposes the project to the local registry.
          </span>
        }
        confirmLabel="Publish"
        busy={publishing}
        danger={false}
      />
    </div>
  );
}

// One deployment surface row: state + an action affordance. Where there is no
// backend yet, the control is disabled (dimmed, not cursor-blocked) with a clear
// label — no fake behavior.
function DeploymentRow({
  name,
  desc,
  active,
  value,
  activeAction,
  idleLabel,
  idleAction,
}: {
  name: string;
  desc: string;
  active: boolean;
  value: string | null;
  activeAction: ReactNode;
  idleLabel: string;
  // When provided, replaces the default disabled idle button (used by the
  // Public row, whose Publish action is wired). Other rows stay disabled until
  // the deployment runner lands (Phase 4), no fake behavior.
  idleAction?: ReactNode;
}) {
  return (
    <Row wrap style={{ alignItems: "center", paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)" }}>
      <div style={{ flex: "1 1 16rem" }}>
        <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
          <span style={{ fontWeight: "var(--fw-semi)" }}>{name}</span>
          {active ? (
            <StatusDot status="ok">live</StatusDot>
          ) : (
            <StatusDot status="stopped">not deployed</StatusDot>
          )}
          {value && <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{value}</span>}
        </Row>
        <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-xs)" }}>{desc}</p>
      </div>
      {active ? activeAction : idleAction ?? (
        <Button size="sm" variant="ghost" disabled title="Lands with the deployment runner (Phase 4)">
          {idleLabel}
        </Button>
      )}
    </Row>
  );
}
