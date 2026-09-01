import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Bot,
  GitBranch,
  GitMerge,
  Trash2,
  FileText,
  ListTodo,
  StickyNote,
  Plus,
  Pencil,
  RefreshCw,
  Upload,
  Store,
  MessagesSquare,
  Users,
  KeyRound,
  FolderGit2,
  GitPullRequest,
  ExternalLink,
} from "lucide-react";
import type { BuildAgent, Project } from "@shared/types";
import { Button, Card, CardGrid, ConfirmDialog, Modal, Notice, PageHeader, Row, Stack, SectionLabel, StateBlock, StatusPill, StatusDot, Tag, Toggle, Skeleton, Textarea, Input, Select, Field, ActionBar, Segmented, catFor, type StatusKind, useEditMode, useCrumbLabel, timeAbsoluteShort } from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";

import {
  type TaskStatus,
  TASK_STATUSES,
  taskStatusKind,
  taskStatusLabel,
  title as taskTitle,
} from "../lib/tasks";
import { chatApi, type ThreadSummary } from "../lib/chat";
import { newChatHref } from "../lib/chatSelection";
import { marketplaceApi, type RegistryItem } from "../lib/marketplace";
import AppServicesPanel from "../components/AppServicesPanel";
import DeploysPanel from "../components/DeploysPanel";
import AutonomyPanel from "../components/AutonomyPanel";
import EnvironmentPanel from "../components/EnvironmentPanel";

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

// GET /api/worktrees/{slug}/repo-state: the canonical-state view (Phase G3).
// bound=false for a purely local project. When bound, each active worktree
// carries its assigned task id, ahead/behind vs origin/<default_branch>, and the
// matched open PR (joined by head branch) when one exists.
type RepoPr = {
  number: number | null;
  url: string;
  state?: string;
  title?: string;
  headRefName?: string;
  isDraft?: boolean;
};
type RepoWorktree = {
  branch_slug: string;
  dir: string;
  task_id: string;
  ahead_behind: { ahead: number; behind: number };
  pr: RepoPr | null;
};
type RepoState = {
  bound: boolean;
  default_branch?: string;
  remote_url?: string | null;
  worktrees?: RepoWorktree[];
  open_prs?: RepoPr[];
};

type Task = {
  id: string;
  text: string;
  status: TaskStatus;
  owner: string;
  parent: string | null;
  created?: string;
  updated?: string;
};

// One shared empty array, so a childless row does not allocate a new one on
// every render (and does not break memoization downstream by identity).
const EMPTY_TASKS: Task[] = [];

// A note in the project's knowledge scope (project:<slug>). The Notes section
// reads and writes this scope so operator notes and agent notes share the one
// substrate that IS injected into chat context. The list carries title/hook/
// updated; the hook is the one-line summary shown as the preview.
type KnowledgeNote = { id: string; title: string; hook: string; updated?: string | null };

// A row from GET /api/agents (the unified registry roster). Distinct from the
// Build agents list (GET /api/agents/build), which is execution state: these are
// DEFINITION-file agents under projects/<slug>/agents/*.md, of any role.
type RosterAgent = {
  id: string;
  slug: string;
  role: string | null;
  name: string;
  purpose: string;
  project_slug: string | null;
  scope: string | null;
  source: string | null;
};

// A resource-scoped capability grant from GET /api/grants (resource_grants[]).
type ResourceGrant = {
  id: string;
  from: string;
  to: string;
  store_uri: string;
  subtree: boolean;
  verbs: string[];
  caveats: Record<string, unknown>;
  created_at: string;
};

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

// Task status type + status-to-kind map + labels now come from lib/tasks (the
// single source of truth). This page previously knew only four of the seven
// statuses and rendered a broken StatusDot for in_review / parked / cancelled.

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// ---- Notes helpers ---------------------------------------------------------
// A note id must match the knowledge store's slug rule ([a-z0-9] segments joined
// by "-"). Derive one from the title, fall back to "note", and always append a
// base36 timestamp so a fresh note never collides with an existing file. Same
// rule as the /notes surface (Notes.tsx freshNoteId).
function freshNoteId(title: string): string {
  const base =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "note";
  return `${base}-${Date.now().toString(36)}`;
}

// Stores hosted by this project (B11). Reads /api/stores?project=<slug>
// which routes through stores.by_host on the backend (entity-model 2026-07-18
// §8.3). Shows a compact list of tier + URI + name; grant edits still live
// in Settings > Grants.
type StoreSummary = {
  uri: string;
  owner_app: string;
  name: string;
  type: string | null;
  host_project?: string | null;
  tier: string;
};
function ProjectStoresSection({ slug }: { slug: string }) {
  const [stores, setStores] = useState<StoreSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/stores?project=${encodeURIComponent(slug)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .then((j) => setStores(j.stores ?? []))
      .catch((e) => setErr(String(e)));
  }, [slug]);
  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <SectionLabel icon={<Store aria-hidden />}>Storage and access</SectionLabel>
      {err ? (
        <StateBlock variant="error" title="Couldn't load stores" hint={<code>GET /api/stores?project={slug} ({err})</code>} />
      ) : stores === null ? (
        <Card><Skeleton width="40%" /></Card>
      ) : stores.length === 0 ? (
        <StateBlock
          title="No project-hosted stores"
          hint="This project owns no stores. Its agents read the shared workshop notes instead."
        />
      ) : (
        <Card compact style={{ padding: "var(--space-3)" }}>
          <Stack gap="sm">
            {stores.map((s) => (
              <Row key={s.uri} between wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Tag hue={catFor(s.tier)}>{s.tier}</Tag>
                  <Link to={`/stores/${encodeURIComponent(s.owner_app)}/${encodeURIComponent(s.name)}`}
                        className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
                    {s.name}
                  </Link>
                  {s.type && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{s.type}</span>}
                </Row>
                <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{s.uri}</span>
              </Row>
            ))}
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              Grant edits + broker allowlist live in{" "}
              <Link to="/settings/grants" style={{ color: "var(--accent)" }}>Settings &gt; Grants</Link>.
            </span>
          </Stack>
        </Card>
      )}
    </section>
  );
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
  // WHICH TREE IT WILL WORK IN. `POST /api/agents/build` has always accepted
  // `branch_slug` and resolved the agent's cwd from it, and no create form ever
  // sent one, so the only working branch pin was reachable from the API alone.
  // Empty means the project's own tree, which is the default and stays the
  // default: nobody who did not ask for a branch gets one.
  const [branchSlug, setBranchSlug] = useState("");
  const [branches, setBranches] = useState<{ branch_slug: string }[] | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetch(`/api/worktrees/${encodeURIComponent(projectSlug)}`)
      .then((r) => (r.ok ? r.json() : []))
      // Not an error state: a project with no branches is the ordinary case,
      // and the control simply offers the one tree there is.
      .then((list) => { if (!cancelled) setBranches(list || []); })
      .catch(() => { if (!cancelled) setBranches([]); });
    return () => { cancelled = true; };
  }, [open, projectSlug]);

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
          branch_slug: branchSlug || null,
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
        {branches !== null && branches.length > 0 && (
          <Field
            label="Working tree"
            help="Its turns run here. A branch keeps its writes off the shared tree; you can move it later."
          >
            <Select
              value={branchSlug}
              onChange={(e) => setBranchSlug(e.target.value)}
              aria-label="Working tree"
            >
              <option value="">The project's own tree</option>
              {branches.map((b) => (
                <option key={b.branch_slug} value={b.branch_slug}>{b.branch_slug}</option>
              ))}
            </Select>
          </Field>
        )}
        {err && <Notice tone="error" detail={err}>Couldn't spawn this agent.</Notice>}
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
  slug,
  childrenOf,
  depth,
  onStatus,
  onDelete,
}: {
  task: Task;
  slug: string;
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
            <option key={s} value={s}>{taskStatusLabel(s)}</option>
          ))}
        </Select>
        <StatusDot status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusDot>
        <span
          className="ws-truncate"
          title={taskTitle(task)}
          style={{
            flex: "1 1 16rem",
            minWidth: 0,
            textDecoration: task.status === "done" ? "line-through" : "none",
            color: task.status === "done" ? "var(--muted)" : "inherit",
          }}
        >
          {taskTitle(task)}
        </span>
        <Link
          to={`/work?scope=${encodeURIComponent(`project:${slug}`)}`}
          className="ws-bc__link"
          title="Open this project's tickets"
          style={{ fontSize: "var(--fs-sm)", whiteSpace: "nowrap" }}
        >
          View in Tickets
        </Link>
        <Button size="sm" variant="danger-ghost" icon aria-label="Delete task" onClick={() => onDelete(task)}>
          <Trash2 aria-hidden />
        </Button>
      </Row>
      {kids.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {kids.map((k) => (
            <TaskRow key={k.id} task={k} slug={slug} childrenOf={childrenOf} depth={depth + 1} onStatus={onStatus} onDelete={onDelete} />
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
  // The project's own chat threads (context_key `project:<slug>`), including any
  // general chat that was later rescoped to this project. Without this section a
  // rescoped chat is unreachable, it looks like it vanished.
  const [chats, setChats] = useState<ThreadSummary[] | null>(null);

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

  // Tasks. One CRUD surface, /api/tasks, scoped to this project. The
  // /api/projects/{slug}/tasks routes that used to back this section are gone:
  // they wrote the same store while skipping dedup, provenance and the queue
  // runner's wake file, so a ticket filed here behaved differently from the
  // identical ticket filed on the Tickets page.
  const taskScope = `project:${slug}`;
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [tasksErr, setTasksErr] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [addText, setAddText] = useState("");
  const [adding, setAdding] = useState(false);
  const [deleteTask, setDeleteTask] = useState<Task | null>(null);
  const [deletingTask, setDeletingTask] = useState(false);

  // Notes (project:<slug> knowledge scope, the substrate injected into context)
  const noteScope = `project:${slug}`;
  const [notes, setNotes] = useState<KnowledgeNote[] | null>(null);
  const [notesErr, setNotesErr] = useState<string | null>(null);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteBody, setNoteBody] = useState("");
  const [postingNote, setPostingNote] = useState(false);
  const [notePostErr, setNotePostErr] = useState<string | null>(null);

  const [agents, setAgents] = useState<BuildAgent[] | null>(null);
  const [spawning, setSpawning] = useState(false);

  // Project-scoped DEFINITION-file agents (registry) + capability grants they hold.
  const [defAgents, setDefAgents] = useState<RosterAgent[] | null>(null);
  const [defAgentsErr, setDefAgentsErr] = useState<string | null>(null);
  const [resourceGrants, setResourceGrants] = useState<ResourceGrant[] | null>(null);
  const [grantsErr, setGrantsErr] = useState<string | null>(null);

  const [worktrees, setWorktrees] = useState<Worktree[] | null>(null);
  const [worktreesErr, setWorktreesErr] = useState<string | null>(null);
  const [worktreesLoading, setWorktreesLoading] = useState(true);
  const [mergeTarget, setMergeTarget] = useState<Worktree | null>(null);
  const [mergeMessage, setMergeMessage] = useState("");
  const [merging, setMerging] = useState(false);
  const [discardTarget, setDiscardTarget] = useState<Worktree | null>(null);
  const [discarding, setDiscarding] = useState(false);

  // Connect-to-GitHub (the Repository section). Create a new private repo or
  // bind an existing remote you own; writes the project's `repo` field.
  const [connectOpen, setConnectOpen] = useState(false);
  const [connectMode, setConnectMode] = useState<"create" | "bind">("create");
  const [bindUrl, setBindUrl] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectErr, setConnectErr] = useState<string | null>(null);

  // Per-branch publish (push + open PR). Bound projects only. Tracks the branch
  // currently in flight, the returned PR per branch, and any per-branch error.
  const [publishingBranch, setPublishingBranch] = useState<string | null>(null);
  const [branchPr, setBranchPr] = useState<Record<string, { url: string; number: number }>>({});
  const [branchPublishErr, setBranchPublishErr] = useState<Record<string, string>>({});

  // Canonical repo-state (default branch, active worktrees + PRs, ahead/behind).
  // Fetched only when the project is bound to a remote.
  const [repoState, setRepoState] = useState<RepoState | null>(null);
  const [repoStateErr, setRepoStateErr] = useState<string | null>(null);
  const [repoStateLoading, setRepoStateLoading] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [cleanupMsg, setCleanupMsg] = useState<string | null>(null);
  // Concurrent-worktrees opt-in (PATCH /api/projects/{slug}). A single boolean
  // config toggle: commit on change with a brief "Saved" indicator, revert on
  // failure. Mirrored locally so the switch reflects the pending write.
  const [concurrent, setConcurrentState] = useState(false);
  const [concurrentSaving, setConcurrentSaving] = useState(false);
  const [concurrentFlash, setConcurrentFlash] = useState(false);

  // Publish-to-marketplace (the Deployments "Public" row).
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishErr, setPublishErr] = useState<string | null>(null);
  const [published, setPublished] = useState<RegistryItem | null>(null);

  useCrumbLabel(project?.title || null);

  // ---- Loaders --------------------------------------------------------------
  const loadChats = useCallback(async () => {
    try {
      const ts = await chatApi.listThreads(`project:${slug}`);
      setChats(ts.filter((t) => t.message_count > 0));
    } catch {
      setChats([]);
    }
  }, [slug]);

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

  const loadRepoState = useCallback(async () => {
    setRepoStateLoading(true);
    setRepoStateErr(null);
    try {
      const r = await fetch(`/api/worktrees/${slug}/repo-state`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setRepoState(await r.json());
    } catch (e) {
      setRepoStateErr(e instanceof Error ? e.message : String(e));
      setRepoState(null);
    } finally {
      setRepoStateLoading(false);
    }
  }, [slug]);

  const loadTasks = useCallback(async () => {
    setTasksErr(null);
    try {
      const r = await fetch(`/api/tasks?scope=${encodeURIComponent(taskScope)}&include_done=true`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const d = await r.json();
      // `items`, not `tasks`: GET /api/tasks answers Page[Task] since 7e4aacbf.
      // The old key read `undefined`, so this panel showed no tasks at all.
      setTasks((d.items ?? []) as Task[]);
    } catch (e) {
      setTasksErr(String(e));
      setTasks([]);
    }
  }, [taskScope]);

  const loadNotes = useCallback(async () => {
    setNotesErr(null);
    try {
      const r = await fetch(`/api/memory/scopes/${encodeURIComponent(noteScope)}/notes`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNotes(await r.json());
    } catch (e) {
      setNotesErr(String(e));
      setNotes([]);
    }
  }, [noteScope]);

  // Definition-file agents scoped to this project (registry roster, source =
  // "definition"), distinct from the Build agents above, which are execution
  // state.
  const loadDefAgents = useCallback(async () => {
    setDefAgentsErr(null);
    try {
      const r = await fetch("/api/agents");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const all: RosterAgent[] = await r.json();
      setDefAgents(all.filter((a) => a.project_slug === slug && a.source === "definition"));
    } catch (e) {
      setDefAgentsErr(String(e));
      setDefAgents([]);
    }
  }, [slug]);

  // All resource-scoped grants; filtered to this project's scope client-side.
  const loadGrants = useCallback(async () => {
    setGrantsErr(null);
    try {
      const r = await fetch("/api/grants");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const d = await r.json();
      setResourceGrants((d.resource_grants ?? []) as ResourceGrant[]);
    } catch (e) {
      setGrantsErr(String(e));
      setResourceGrants([]);
    }
  }, []);

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
  // Repo-state is a bound-project concern; only fetch once the project record
  // shows a remote binding (avoids a guaranteed {bound:false} round-trip).
  useEffect(() => { if (project?.repo) loadRepoState(); }, [project?.repo, loadRepoState]);
  // Mirror the persisted opt-in into the local toggle whenever the record loads.
  useEffect(() => {
    const v = (project as (Project & { concurrent_worktrees?: boolean }) | null)?.concurrent_worktrees;
    setConcurrentState(Boolean(v));
  }, [project]);
  useEffect(() => { loadTasks(); }, [loadTasks]);
  useEffect(() => { loadNotes(); }, [loadNotes]);
  useEffect(() => { loadChats(); }, [loadChats]);
  useEffect(() => { loadDefAgents(); }, [loadDefAgents]);
  useEffect(() => { loadGrants(); }, [loadGrants]);
  useEffect(() => { loadAll(); }, [loadAll]);

  // ---- Task mutations -------------------------------------------------------
  const addTask = useCallback(async () => {
    const text = addText.trim();
    if (!text) return;
    setAdding(true);
    try {
      await fetch(`/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: taskScope, text }),
      });
      setAddText("");
      await loadTasks();
    } finally {
      setAdding(false);
    }
  }, [addText, taskScope, loadTasks]);

  const setTaskStatus = useCallback(async (id: string, status: TaskStatus) => {
    setTasks((prev) => prev?.map((t) => (t.id === id ? { ...t, status } : t)) ?? prev);
    await fetch(`/api/tasks/${encodeURIComponent(taskScope)}/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    await loadTasks();
  }, [taskScope, loadTasks]);

  const doDeleteTask = useCallback(async () => {
    if (!deleteTask) return;
    setDeletingTask(true);
    try {
      await fetch(
        `/api/tasks/${encodeURIComponent(taskScope)}/${encodeURIComponent(deleteTask.id)}`,
        { method: "DELETE" },
      );
      await loadTasks();
    } finally {
      setDeletingTask(false);
      setDeleteTask(null);
    }
  }, [deleteTask, taskScope, loadTasks]);

  // ---- Notes mutation -------------------------------------------------------
  // Write a fresh note into the project knowledge scope via the per-note PUT
  // route (same shape as the /notes surface). Title or body is enough.
  const postNote = useCallback(async () => {
    const title = noteTitle.trim();
    const body = noteBody;
    if (!title && !body.trim()) return;
    setPostingNote(true);
    setNotePostErr(null);
    try {
      const id = freshNoteId(title);
      const r = await fetch(
        `/api/memory/scopes/${encodeURIComponent(noteScope)}/notes/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, content: body, hook: "" }),
        },
      );
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNoteTitle("");
      setNoteBody("");
      await loadNotes();
    } catch (e) {
      setNotePostErr(String(e));
    } finally {
      setPostingNote(false);
    }
  }, [noteTitle, noteBody, noteScope, loadNotes]);

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

  // ---- Connect to GitHub ----------------------------------------------------
  // Create a new private repo (workshop-<slug> for a scratch project) or bind an
  // existing owned remote. The server rejects any blocked owner with
  // a 400, which we surface inline rather than crashing.
  const doConnect = useCallback(async () => {
    setConnecting(true);
    setConnectErr(null);
    try {
      const payload =
        connectMode === "create"
          ? { mode: "create" as const }
          : { mode: "bind" as const, remote_url: bindUrl.trim() };
      const r = await fetch(`/api/projects/${slug}/connect-github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      await loadAll();
      setConnectOpen(false);
      setBindUrl("");
    } catch (e) {
      setConnectErr(e instanceof Error ? e.message : String(e));
    } finally {
      setConnecting(false);
    }
  }, [connectMode, bindUrl, slug, loadAll]);

  // ---- Publish a branch (push + open PR) ------------------------------------
  const doPublishBranch = useCallback(
    async (w: Worktree) => {
      setPublishingBranch(w.branch_slug);
      setBranchPublishErr((prev) => {
        const next = { ...prev };
        delete next[w.branch_slug];
        return next;
      });
      try {
        const r = await fetch(`/api/worktrees/${slug}/${w.branch_slug}/publish`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const d = await r.json();
        if (d?.pr?.url) {
          setBranchPr((prev) => ({ ...prev, [w.branch_slug]: d.pr }));
          window.open(d.pr.url, "_blank", "noopener,noreferrer");
        }
        await loadWorktrees();
      } catch (e) {
        setBranchPublishErr((prev) => ({
          ...prev,
          [w.branch_slug]: e instanceof Error ? e.message : String(e),
        }));
      } finally {
        setPublishingBranch(null);
      }
    },
    [slug, loadWorktrees],
  );

  // ---- Repo state: cleanup + concurrency opt-in -----------------------------
  const doCleanup = useCallback(async () => {
    setCleaningUp(true);
    setCleanupMsg(null);
    try {
      const r = await fetch(`/api/worktrees/${slug}/cleanup`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const d = await r.json();
      const n = (d.removed ?? []).length;
      setCleanupMsg(n === 0 ? "Nothing to clean up" : `Removed ${n} merged ${n === 1 ? "branch" : "branches"}`);
      await loadRepoState();
      await loadWorktrees();
    } catch (e) {
      setCleanupMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setCleaningUp(false);
    }
  }, [slug, loadRepoState, loadWorktrees]);

  const setConcurrent = useCallback(
    async (v: boolean) => {
      setConcurrentState(v);
      setConcurrentSaving(true);
      setConcurrentFlash(false);
      try {
        const r = await fetch(`/api/projects/${slug}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ concurrent_worktrees: v }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        setConcurrentFlash(true);
        window.setTimeout(() => setConcurrentFlash(false), 1600);
      } catch {
        setConcurrentState(!v); // revert on failure
      } finally {
        setConcurrentSaving(false);
      }
    },
    [slug],
  );

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

  // INDEXED, because this is called once per rendered row and recursively for
  // that row's children. As a `filter` it was a full scan of every visible task
  // per row, so drawing the tree cost O(tasks^2) on every poll tick, and the
  // only thing standing between that and a locked-up page was the `> 400` bail
  // out at the render site. Building the index once is one pass.
  const childIndex = useMemo(() => {
    const m = new Map<string, Task[]>();
    for (const t of visibleTasks) {
      const key = `${t.owner}\0${t.parent ?? ""}`;
      const list = m.get(key);
      if (list) list.push(t);
      else m.set(key, [t]);
    }
    return m;
  }, [visibleTasks]);

  const childrenOf = useCallback(
    (parent: string | null, owner: string) =>
      childIndex.get(`${owner}\0${parent ?? ""}`) ?? EMPTY_TASKS,
    [childIndex],
  );

  const agentName = useCallback(
    (owner: string) => (agents ?? []).find((a) => a.slug === owner)?.name ?? owner,
    [agents],
  );

  const doneCount = useMemo(() => (tasks ?? []).filter((t) => t.status === "done").length, [tasks]);

  // Grants held by this project's scope: either the project scope itself
  // (`project:<slug>`) or any of its definition-file agents (`agent:<id|slug>`).
  const grantScopes = useMemo(() => {
    const s = new Set<string>([`project:${slug}`]);
    for (const a of defAgents ?? []) {
      s.add(`agent:${a.id}`);
      s.add(`agent:${a.slug}`);
    }
    return s;
  }, [defAgents, slug]);

  const heldGrants = useMemo(
    () => (resourceGrants ?? []).filter((g) => grantScopes.has(g.to)),
    [resourceGrants, grantScopes],
  );

  const grantAgentLabel = useCallback(
    (to: string) => {
      const id = to.startsWith("agent:") ? to.slice("agent:".length) : "";
      const a = (defAgents ?? []).find((x) => x.id === id || x.slug === id);
      if (a) return a.name;
      if (to === `project:${slug}`) return "project scope";
      return to;
    },
    [defAgents, slug],
  );

  if (err) {
    return (
      <div>
        <PageHeader title="Project" />
        <StateBlock
          variant="error"
          title="Couldn't load project"
          hint={<code>GET /api/projects/{slug} ({err})</code>}
          action={<Button size="sm" onClick={loadAll}>Retry</Button>}
        />
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
          <Row wrap>
            <StatusPill status={stateKind(project.state)}>{project.state}</StatusPill>
            <Tag hue="slate">{project.sensitivity}</Tag>
            <Button size="sm" variant="primary" onClick={() => nav(`/chat/${encodeURIComponent(`project:${slug}`)}`)}>
              <MessagesSquare aria-hidden /> Open chat
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { loadAll(); loadTasks(); loadNotes(); loadWorktrees(); loadChats(); loadDefAgents(); loadGrants(); }}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </Row>
        }
      />

      {/* --- Project chat (incl. rescoped planning chats) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<MessagesSquare aria-hidden />}>Chat</SectionLabel>
          {/* NEW chat, so it states that intent — the two "Open chat" buttons on
              this page deliberately restore the last one instead. */}
          <Button variant="ghost" size="sm" onClick={() => nav(newChatHref(`project:${slug}`))}>
            <Plus aria-hidden /> New chat
          </Button>
        </Row>
        {chats === null ? (
          <Card><Skeleton width="40%" /></Card>
        ) : chats.length === 0 ? (
          <StateBlock
            title="No project chat yet"
            hint="Open a chat to plan or drive this project. Planning chats you convert into this project also land here."
            action={
              <Button variant="primary" size="sm" onClick={() => nav(`/chat/${encodeURIComponent(`project:${slug}`)}`)}>
                <MessagesSquare aria-hidden /> Open chat
              </Button>
            }
          />
        ) : (
          <CardGrid>
            {chats.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/chat/${encodeURIComponent(`project:${slug}`)}?thread=${encodeURIComponent(c.id)}`}
                  style={{ textDecoration: "none", color: "inherit" }}
                >
                  <Card interactive>
                    <Row between style={{ gap: "var(--space-2)" }}>
                      <h3 className="ws-card__title" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <MessagesSquare size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {c.title || "Untitled chat"}
                      </h3>
                      <span className="ws-card__meta" style={{ flex: "0 0 auto" }}>{c.message_count} msg</span>
                    </Row>
                  </Card>
                </Link>
              </li>
            ))}
          </CardGrid>
        )}
      </section>

      {/* --- Autonomy (per-project Ralph loop): kept prominent, right under the
             project header/chat so per-project autonomy is immediately visible. --- */}
      <AutonomyPanel slug={slug} />

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
            action={
              <Button variant="primary" size="sm" onClick={() => setSpawning(true)}>
                <Bot aria-hidden /> Start building
              </Button>
            }
          />
        ) : (
          <CardGrid>
            {agents.map((a) => (
              <li key={a.slug}>
                <Link to={`/agents/${a.slug}/chat`} style={{ textDecoration: "none", color: "inherit" }}>
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

      {/* --- Agents (definition files under projects/<slug>/agents) --- */}
      {/* Distinct from Build agents above: these are the registry's declarative
          agent definitions scoped to this project, of any role. Each links to
          its AgentDetail config page. */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Users aria-hidden />}>Agents</SectionLabel>
        </Row>
        {defAgents === null ? (
          <Card><Skeleton width="40%" /></Card>
        ) : defAgentsErr ? (
          <StateBlock
            variant="error"
            title="Couldn't load project agents"
            hint={<code>GET /api/agents ({defAgentsErr})</code>}
            action={<Button size="sm" onClick={loadDefAgents}>Retry</Button>}
          />
        ) : defAgents.length === 0 ? (
          <StateBlock
            title="No agent definitions for this project"
            hint="Nothing yet. Define an agent to give this project one of its own."
          />
        ) : defAgents.length > 100 ? (
          <StateBlock title="Too many agents to render" hint="Retire unused agent definitions." />
        ) : (
          <CardGrid>
            {defAgents.map((a) => (
              <li key={a.slug}>
                <Link to={`/agents/${a.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card interactive>
                    <Row between style={{ gap: "var(--space-2)" }}>
                      <h3 className="ws-card__title" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <Bot size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name}
                      </h3>
                      {a.role
                        ? <Tag hue={catFor(`role:${a.role}`)}>{a.role}</Tag>
                        : <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>no role</span>}
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

      {/* --- Storage and access (B11) ---
           Stores hosted by this project (entity-model 2026-07-18 §8.3). Uses
           GET /api/stores?project=<slug> which routes through stores.by_host.
           Read-only for now; tier + grants + allowlist edits still live in
           Settings > Grants + Settings > Store policies. */}
      <ProjectStoresSection slug={slug} />

      {/* --- Grants (capabilities held by this project's agents/scope) --- */}
      {/* Read-only view of resource-scoped grants (GET /api/grants) whose holder
          is this project's scope or one of its definition-file agents. Minting /
          revoking lives on AgentDetail + Settings > Grants, not here. */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<KeyRound aria-hidden />}>Grants</SectionLabel>
        {resourceGrants === null ? (
          <Card><Skeleton width="45%" /></Card>
        ) : grantsErr ? (
          <StateBlock
            variant="error"
            title="Couldn't load grants"
            hint={<code>GET /api/grants ({grantsErr})</code>}
            action={<Button size="sm" onClick={loadGrants}>Retry</Button>}
          />
        ) : heldGrants.length === 0 ? (
          <StateBlock
            title="No grants held"
            hint="This project's agents hold no capability grants over any Store. Mint one from an agent's detail page (Grants section) to give it scoped access."
          />
        ) : heldGrants.length > 100 ? (
          <StateBlock title="Too many grants to render" hint="Revoke unused grants in Settings > Grants." />
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {heldGrants.map((g) => {
              const caveatKeys = Object.keys(g.caveats || {});
              return (
                <li key={g.id}>
                  <Card style={{ marginBottom: "var(--space-2)" }}>
                    <Row between wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <span className="ws-mono" style={{ flex: "1 1 16rem", overflowWrap: "anywhere" }}>
                        {g.store_uri}{g.subtree ? "/*" : ""}
                      </span>
                      <Tag hue="slate">{grantAgentLabel(g.to)}</Tag>
                    </Row>
                    <Row wrap style={{ gap: "var(--space-1)", marginTop: "var(--space-2)" }}>
                      {g.verbs.map((v) => (
                        <Tag key={v} hue={catFor(v)}>{v}</Tag>
                      ))}
                    </Row>
                    {caveatKeys.length > 0 && (
                      <p className="ws-muted" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-xs)" }}>
                        caveats: {caveatKeys.map((k) => `${k}=${String((g.caveats as Record<string, unknown>)[k])}`).join(", ")}
                      </p>
                    )}
                  </Card>
                </li>
              );
            })}
          </ul>
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
              hint="The brief is the goal and the constraints your build agents read every turn."
              action={
                <Button variant="primary" size="sm" onClick={briefEdit.begin}>
                  <Pencil aria-hidden /> Write the brief
                </Button>
              }
            />
          ) : (
            <Markdown>{brief}</Markdown>
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
              <Notice
                tone="error"
                where={`/api/tasks?scope=${taskScope}`}
                detail={tasksErr}
                action={<Button size="sm" variant="ghost" onClick={loadTasks}>Retry</Button>}
              >
                Couldn't load tasks.
              </Notice>
            ) : ownerOrder.length === 0 ? (
              <StateBlock
                title={tasks.length === 0 ? "No tasks yet" : "No open tasks"}
                hint={tasks.length === 0
                  ? "Add a task above, or a build agent can create its own."
                  : "All tasks are done. Toggle “Show done” to see them."}
              />
            ) : visibleTasks.length > 400 ? (
              <StateBlock title="Too many tasks to render" hint="Close out or delete completed tasks." />
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
                            slug={slug}
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

      {/* --- Notes (project:<slug> knowledge scope, injected into chat context) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between wrap style={{ alignItems: "baseline" }}>
          <SectionLabel icon={<StickyNote aria-hidden />}>Notes</SectionLabel>
          <Link to={`/notes?scope=${encodeURIComponent(noteScope)}`} className="ws-bc__link">
            View all in Notes
          </Link>
        </Row>
        <Card>
          <Stack>
            <Input
              value={noteTitle}
              onChange={(e) => setNoteTitle(e.target.value)}
              placeholder="Note title"
              aria-label="Note title"
            />
            <Textarea
              value={noteBody}
              onChange={(e) => setNoteBody(e.target.value)}
              placeholder="Leave a note for this project and its agents."
              rows={3}
            />
            <Row>
              <Button variant="primary" onClick={postNote} loading={postingNote} disabled={!noteTitle.trim() && !noteBody.trim()}>
                <Plus aria-hidden /> Save note
              </Button>
            </Row>
            {notePostErr && <p className="ws-field__error">{notePostErr}</p>}

            {notes === null ? (
              <Stack gap="sm">
                <Skeleton width="65%" />
                <Skeleton width="50%" />
              </Stack>
            ) : notesErr ? (
              <Notice
                tone="error"
                where={`/api/memory/scopes/${noteScope}/notes`}
                detail={notesErr}
                action={<Button size="sm" variant="ghost" onClick={loadNotes}>Retry</Button>}
              >
                Couldn't load notes.
              </Notice>
            ) : notes.length === 0 ? (
              <StateBlock
                title="No notes yet"
                hint="Notes here are shared with this project's agents and go into its chat context."
              />
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {notes.map((n) => (
                  <li key={n.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <span style={{ fontWeight: 600, wordBreak: "break-word" }}>{n.title || n.id}</span>
                      {n.updated && (
                        <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{timeAbsoluteShort(n.updated)}</span>
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

      {/* --- Repository (GitHub binding) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<FolderGit2 aria-hidden />}>Repository</SectionLabel>
        <Card>
          {project.repo ? (
            <Stack gap="sm">
              <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                <FolderGit2 size={16} aria-hidden />
                <a
                  href={project.repo.remote_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ws-bc__link ws-mono"
                  style={{ fontWeight: "var(--fw-semi)" }}
                >
                  {project.repo.owner}/{project.repo.name}
                </a>
                <StatusPill status="ok">connected</StatusPill>
                <Tag hue="slate">{project.repo.provider}</Tag>
              </Row>
              <Row wrap className="ws-muted" style={{ gap: "var(--space-4)", fontSize: "var(--fs-sm)" }}>
                <span>
                  <GitBranch size={14} aria-hidden /> default branch{" "}
                  <code className="ws-mono">{project.repo.default_branch}</code>
                </span>
                <span>connected {timeAbsoluteShort(project.repo.connected_at)}</span>
              </Row>

              {/* Concurrent-worktrees opt-in. A single config boolean: the Toggle
                  commits on change (PATCH) with a brief Saved indicator, and
                  reverts if the write fails. */}
              <Row
                between
                wrap
                style={{ alignItems: "flex-start", gap: "var(--space-2)", paddingTop: "var(--space-2)" }}
              >
                <div style={{ flex: "1 1 20rem" }}>
                  <Toggle
                    checked={concurrent}
                    onChange={setConcurrent}
                    disabled={concurrentSaving}
                    label="Concurrent worktrees (run tasks in isolated branches)"
                  />
                  <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-xs)" }}>
                    When on, the dispatcher gives each task its own worktree off{" "}
                    <code className="ws-mono">origin/{repoState?.default_branch ?? project.repo.default_branch}</code>{" "}
                    so tasks run in parallel without contending on one working tree.
                  </p>
                </div>
                {concurrentFlash && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>Saved</span>
                )}
              </Row>

              {/* Active worktrees (canonical repo-state). Four list states. */}
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "var(--space-3)" }}>
                <Row between wrap style={{ marginBottom: "var(--space-2)", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ fontWeight: "var(--fw-semi)", fontSize: "var(--fs-sm)" }}>Active worktrees</span>
                  <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    {cleanupMsg && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{cleanupMsg}</span>
                    )}
                    <Button size="sm" variant="ghost" onClick={doCleanup} loading={cleaningUp}>
                      <Trash2 aria-hidden /> Clean up merged
                    </Button>
                    <Button size="sm" variant="ghost" onClick={loadRepoState} disabled={repoStateLoading}>
                      <RefreshCw aria-hidden /> Refresh
                    </Button>
                  </Row>
                </Row>

                {repoStateLoading && !repoState ? (
                  <Stack gap="sm">
                    <Skeleton width="60%" />
                    <Skeleton width="45%" />
                  </Stack>
                ) : repoStateErr ? (
                  <Notice
                    tone="error"
                    where={`/api/worktrees/${slug}/repo-state`}
                    detail={repoStateErr}
                    action={<Button size="sm" variant="ghost" onClick={loadRepoState}>Retry</Button>}
                  >
                    Couldn't load repo state.
                  </Notice>
                ) : (repoState?.worktrees ?? []).length === 0 ? (
                  <StateBlock
                    title="No active worktrees"
                    hint={
                      <>
                        Working on <code className="ws-mono">{repoState?.default_branch ?? project.repo.default_branch}</code>.
                        Create a branch below, or a task allocates one when concurrent worktrees is on.
                      </>
                    }
                  />
                ) : (repoState?.worktrees ?? []).length > 50 ? (
                  <StateBlock
                    title="Too many worktrees to render"
                    hint="Run Clean up merged, or merge and discard old branches."
                  />
                ) : (
                  <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                    {(repoState?.worktrees ?? []).map((w) => (
                      <li key={w.branch_slug} style={{ borderBottom: "1px solid var(--border)", padding: "var(--space-2) 0" }}>
                        <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                          <GitBranch size={14} aria-hidden />
                          <span className="ws-mono" style={{ fontSize: "var(--fs-sm)", flex: "1 1 12rem" }}>{w.branch_slug}</span>
                          <Tag hue={catFor(w.task_id)}>task {w.task_id}</Tag>
                          <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>
                            {w.ahead_behind.ahead} ahead · {w.ahead_behind.behind} behind
                          </span>
                          {w.pr ? (
                            <a href={w.pr.url} target="_blank" rel="noopener noreferrer" style={{ textDecoration: "none" }}>
                              <Button size="sm" variant="ghost">
                                <GitPullRequest aria-hidden /> PR #{w.pr.number}
                                <ExternalLink size={14} aria-hidden />
                              </Button>
                            </a>
                          ) : (
                            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>no PR</span>
                          )}
                        </Row>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Stack>
          ) : (
            <Row between wrap style={{ alignItems: "center" }}>
              <p className="ws-muted" style={{ margin: 0, flex: "1 1 18rem", fontSize: "var(--fs-sm)" }}>
                Not connected to a remote. Connect a GitHub repo to push branches and open PRs.
              </p>
              <Button
                size="sm"
                variant="primary"
                onClick={() => { setConnectMode("create"); setBindUrl(""); setConnectErr(null); setConnectOpen(true); }}
              >
                <FolderGit2 aria-hidden /> Connect GitHub
              </Button>
            </Row>
          )}
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
                hint={<code>GET /api/worktrees/{slug} ({worktreesErr})</code>}
                action={<Button size="sm" onClick={loadWorktrees}>Retry</Button>}
              />
            </div>
          ) : sideBranches.length === 0 ? (
            <p className="ws-muted" style={{ margin: "var(--space-3) 0 0", fontSize: "var(--fs-sm)" }}>
              No side branches; working on main.
            </p>
          ) : sideBranches.length > 50 ? (
            <div style={{ paddingTop: "var(--space-3)" }}>
              <StateBlock title="Too many branches" hint="Merge or discard old ones." />
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {sideBranches.map((w) => (
                <li key={w.branch_slug} style={{ borderBottom: "1px solid var(--border)", padding: "var(--space-3) 0" }}>
                  <Row wrap style={{ alignItems: "center" }}>
                    <GitBranch size={14} aria-hidden />
                    <span className="ws-mono" style={{ fontSize: "var(--fs-sm)", flex: "1 1 12rem" }}>{w.branch_slug}</span>
                    {/* Condensed, aligned, equal-height actions menu. The per-branch
                        Diff button was removed: it pointed at /projects/:slug/diff,
                        a route that never existed (always 404'd). Branch diffs live
                        in the builder workbench; add a real diff surface here if wanted. */}
                    <Row style={{ gap: "var(--space-2)" }}>
                      {/* Publish (push + open PR) only when the project is bound to a remote. */}
                      {project.repo &&
                        (branchPr[w.branch_slug] ? (
                          <a
                            href={branchPr[w.branch_slug].url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ textDecoration: "none" }}
                          >
                            <Button size="sm" variant="ghost">
                              <GitPullRequest aria-hidden /> PR #{branchPr[w.branch_slug].number}
                              <ExternalLink size={14} aria-hidden />
                            </Button>
                          </a>
                        ) : (
                          <Button
                            size="sm"
                            variant="default"
                            loading={publishingBranch === w.branch_slug}
                            disabled={publishingBranch !== null}
                            onClick={() => doPublishBranch(w)}
                          >
                            <GitPullRequest aria-hidden /> Publish
                          </Button>
                        ))}
                      <Button size="sm" variant="default" onClick={() => { setMergeTarget(w); setMergeMessage(""); }}>
                        <GitMerge aria-hidden /> Merge
                      </Button>
                      <Button size="sm" variant="danger-ghost" icon aria-label="Discard branch" onClick={() => setDiscardTarget(w)}>
                        <Trash2 aria-hidden />
                      </Button>
                    </Row>
                  </Row>
                  {branchPublishErr[w.branch_slug] && (
                    <p style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-xs)", color: "var(--danger)" }}>
                      POST /api/worktrees/{slug}/{w.branch_slug}/publish: {branchPublishErr[w.branch_slug]}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* --- App (multi-service runner) --- */}
      <AppServicesPanel slug={slug} />

      {/* --- Deploys (per-project target bindings) --- */}
      <DeploysPanel slug={slug} />

      {/* --- Environment / secrets registry --- */}
      <EnvironmentPanel slug={slug} />

      {/* --- Share (publish to the local marketplace) --- */}
      {/* Run above starts the project's app_runner services locally. Deploys above
          ships a build to a hosted target (Cloudflare Pages, ...). Share below cuts
          a local release and lists it in the workshop marketplace where anyone here
          can install (fork) it. The three surfaces are distinct on purpose. */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Store aria-hidden />}>Share</SectionLabel>
        <Card>
          <Row between wrap style={{ alignItems: "center" }}>
            <div style={{ flex: "1 1 18rem" }}>
              <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <span style={{ fontWeight: "var(--fw-semi)" }}>Marketplace</span>
                {published || project.surfaces.public != null ? (
                  <StatusDot status="ok">published{published ? ` · v${published.version}` : ""}</StatusDot>
                ) : (
                  <StatusDot status="stopped">not published</StatusDot>
                )}
              </Row>
              <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-xs)" }}>
                Cut a local release and list it in the marketplace, where anyone on this workshop can install (fork) it.
              </p>
            </div>
            {published || project.surfaces.public != null ? (
              <Row wrap style={{ gap: "var(--space-2)" }}>
                <Link to="/marketplace" style={{ textDecoration: "none" }}>
                  <Button size="sm" variant="ghost"><Store aria-hidden /> View in marketplace</Button>
                </Link>
                <Button size="sm" variant="ghost" onClick={() => { setPublishErr(null); setConfirmPublish(true); }}>
                  <Upload aria-hidden /> Publish update
                </Button>
              </Row>
            ) : (
              <Button size="sm" variant="primary" onClick={() => { setPublishErr(null); setConfirmPublish(true); }}>
                <Upload aria-hidden /> Publish
              </Button>
            )}
          </Row>
          {publishErr && (
            <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: "var(--space-2) 0 0" }}>
              POST /api/base-apps/registry/publish: {publishErr}
            </p>
          )}
        </Card>
      </section>

      <SpawnAgentModal
        open={spawning}
        onCancel={() => setSpawning(false)}
        projectSlug={project.slug}
        projectTitle={project.title}
        onCreated={(agentSlug) => {
          setSpawning(false);
          nav(`/agents/${agentSlug}/chat`);
        }}
      />

      <Modal
        open={connectOpen}
        onClose={() => { if (!connecting) setConnectOpen(false); }}
        title="Connect GitHub"
        actions={
          <>
            <Button onClick={() => setConnectOpen(false)} disabled={connecting}>Cancel</Button>
            <Button
              variant="primary"
              onClick={doConnect}
              loading={connecting}
              disabled={connecting || (connectMode === "bind" && !bindUrl.trim())}
            >
              {connectMode === "create" ? "Create repo" : "Bind repo"}
            </Button>
          </>
        }
      >
        <Stack>
          <Segmented
            ariaLabel="Connect mode"
            value={connectMode}
            onChange={(m) => { setConnectMode(m); setConnectErr(null); }}
            options={[
              { value: "create", label: "Create new" },
              { value: "bind", label: "Bind existing" },
            ]}
          />
          {connectMode === "create" ? (
            <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
              Creates a new private repository under this install's configured GitHub account and
              pushes the current project. A scratch project lands as{" "}
              <code className="ws-mono">workshop-{slug}</code>.
            </p>
          ) : (
            <Field label="Remote URL" help="An existing repository you own.">
              <Input
                value={bindUrl}
                onChange={(e) => setBindUrl(e.target.value)}
                placeholder="https://github.com/your-account/my-repo.git"
                spellCheck={false}
                autoFocus
              />
            </Field>
          )}
          {connectErr && (
            <Notice tone="error" detail={connectErr}>Couldn't connect.</Notice>
          )}
        </Stack>
      </Modal>

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

