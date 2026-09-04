import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  File, Folder, FolderOpen, GitCommit, Play, RefreshCw, RotateCcw, Save, Square,
  Trash2, ExternalLink, Plus, FileCode, MonitorPlay, GitCompare, FolderTree,
  Pencil, PanelsTopLeft, ScrollText, StickyNote, X,
} from "lucide-react";
import type { BuildAgent } from "@shared/types";
import type { components } from "../api/schema.gen";
import { queryKeys } from "../lib/queryKeys";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  Modal,
  PageHeader,
  Row,
  Segmented,
  Select,
  Stack,
  StateBlock,
  StatusPill,
  Textarea,
  type SegmentedOption,
  type StatusKind,
  Notice,
  useCrumbLabel,
  shortHttpError,
} from "@workshop/ui";
import Chat from "../components/Chat";
import { userStorage } from "../lib/storage";

// Build-agent surface = chat-first. Opening a builder lands you in the CHAT (the
// primary surface, full-width). The coding tools — file tree, editor, live
// preview, diff/commit, system prompt, interventions — live in ONE secondary
// "Workbench" panel toggled from the header. There is a SINGLE view control (the
// Workbench toggle); the chat carries no separate expand button here, so the
// old "two expanders, one worked" confusion is gone. On mobile the Workbench
// opens as a full-screen overlay over the chat.

type TreeNode = {
  name: string;
  path: string;
  kind: "dir" | "file";
  size?: number | null;
  children?: TreeNode[];
};

// Workbench center views. Editor/Preview/Diff are the code panes; Prompt (the
// agent's PROJECT.md) and Notes (interventions) are the steering docs — all one
// Segmented switch so the workbench is a single, flat surface.
type PaneTab = "editor" | "preview" | "diff" | "prompt" | "notes";
const WORKBENCH_KEY = "ws-agent-build-workbench";

type Worktree = {
  branch_slug: string;
  branch: string;
  dir: string;
};

type PreviewStatus = {
  running: boolean;
  pid?: number;
  port?: number;
  command?: string;
  started_at?: number;
  suggested?: { command: string; kind: string };
  suggested_port?: number;
  public_url?: string;
};

const stateKind = (s?: string): StatusKind => {
  switch (s) {
    case "running": return "running";
    case "idle": return "ok";
    case "parked": return "warn";
    case "retired": return "stopped";
    default: return "warn";
  }
};

// The status bar shows three INDEPENDENT axes (agent lifecycle, CLI session,
// preview server), so each pill names its axis. "working" reads more honestly
// than the raw "running" flag, which used to ratchet on forever.
const agentStateWord = (s?: string): string => {
  switch (s) {
    case "running": return "working";
    case "idle": return "idle";
    case "parked": return "parked";
    case "retired": return "retired";
    default: return s || "unknown";
  }
};

// Editor/Preview/Diff is a view switch (view preference) — a Segmented control
// reads more clearly as "pick a view" than tab underlines, and keeps one height.
const PANE_VIEWS: SegmentedOption<PaneTab>[] = [
  { value: "editor", label: <><FileCode size={15} aria-hidden /> Editor</> },
  { value: "preview", label: <><MonitorPlay size={15} aria-hidden /> Preview</> },
  { value: "diff", label: <><GitCompare size={15} aria-hidden /> Diff</> },
  { value: "prompt", label: <><ScrollText size={15} aria-hidden /> Prompt</> },
  { value: "notes", label: <><StickyNote size={15} aria-hidden /> Notes</> },
];

const PANE_HELP: Record<PaneTab, string> = {
  editor: "The file selected in Files.",
  preview: "The live running app (dev server).",
  diff: "Working-tree changes vs HEAD, with commit.",
  prompt: "The agent's system prompt (PROJECT.md), read first every turn.",
  notes: "Interventions the agent picks up between turns.",
};

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

// Status-letter -> human label + tone. xy is two chars from `git status --porcelain`:
// first = index, second = worktree. We collapse to a single label for the UI.
const statusLabel = (xy: string): { text: string; tone: "ok" | "warn" | "err" | "muted" } => {
  const x = xy[0] || " ";
  const y = xy[1] || " ";
  if (x === "?" || y === "?") return { text: "new", tone: "ok" };
  if (x === "A" || y === "A") return { text: "added", tone: "ok" };
  if (x === "D" || y === "D") return { text: "deleted", tone: "err" };
  if (x === "R" || y === "R") return { text: "renamed", tone: "warn" };
  if (x === "M" || y === "M") return { text: "modified", tone: "warn" };
  return { text: xy, tone: "muted" };
};

function DiffBlock({ diff }: { diff: string }) {
  // Each line gets a class. Diff-meta lines are muted; +/- get tinted bands.
  // Colors come from --ok / --ok-soft / --danger / --danger-soft.
  const lines = diff.split("\n");
  return (
    <pre
      style={{
        margin: 0, padding: "0.5rem",
        background: "var(--bg-inset)", borderRadius: "var(--radius-sm)",
        fontSize: "var(--fs-xs)", fontFamily: "ui-monospace, monospace",
        overflow: "auto", maxHeight: "50vh",
      }}
    >
      {lines.map((ln, i) => {
        let bg = "transparent";
        let color = "var(--fg)";
        if (ln.startsWith("+++") || ln.startsWith("---") || ln.startsWith("diff ") || ln.startsWith("index ") || ln.startsWith("@@")) {
          color = "var(--muted)";
        } else if (ln.startsWith("+")) {
          bg = "var(--ok-soft)";
          color = "var(--ok)";
        } else if (ln.startsWith("-")) {
          bg = "var(--danger-soft)";
          color = "var(--danger)";
        }
        return (
          <div key={i} style={{ background: bg, color, padding: "0 0.3rem" }}>
            {ln || " "}
          </div>
        );
      })}
    </pre>
  );
}

function FileTreeNode({ node, depth, onPick, picked }: {
  node: TreeNode;
  depth: number;
  onPick: (path: string) => void;
  picked: string | null;
}) {
  const [open, setOpen] = useState(depth < 1);
  const isPicked = picked === node.path;
  if (node.kind === "dir") {
    return (
      <li>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          style={{
            display: "flex", alignItems: "center", gap: "0.4rem", width: "100%",
            background: "transparent", border: 0, color: "inherit", textAlign: "left",
            padding: "0.15rem 0.3rem", cursor: "pointer", fontSize: "var(--fs-sm)",
            paddingLeft: `${depth * 0.9 + 0.3}rem`,
          }}
        >
          {open ? <FolderOpen size={14} aria-hidden /> : <Folder size={14} aria-hidden />}
          <span>{node.name}</span>
        </button>
        {open && node.children && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {node.children.map((c) => (
              <FileTreeNode key={c.path} node={c} depth={depth + 1} onPick={onPick} picked={picked} />
            ))}
          </ul>
        )}
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(node.path)}
        style={{
          display: "flex", alignItems: "center", gap: "0.4rem", width: "100%",
          background: isPicked ? "var(--bg-inset)" : "transparent",
          border: 0, color: "inherit", textAlign: "left",
          padding: "0.15rem 0.3rem", cursor: "pointer", fontSize: "var(--fs-sm)",
          paddingLeft: `${depth * 0.9 + 0.3}rem`,
        }}
      >
        <File size={14} aria-hidden />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.name}</span>
      </button>
    </li>
  );
}

function NewBranchModal({
  open, projectSlug, onCancel, onCreated,
}: {
  open: boolean;
  projectSlug: string;
  onCancel: () => void;
  onCreated: (branchSlug: string) => void;
}) {
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/worktrees/${projectSlug}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch_slug: slug }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      onCreated(slug);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="New branch"
      actions={
        <>
          <Button onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={!slug.trim() || busy} loading={busy}>
            Create branch
          </Button>
        </>
      }
    >
      <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
        Creates a git worktree branched from this project's main. The agent edits this
        copy without touching main; you merge or discard from the Project page.
      </p>
      <Field label="Branch slug" required help="kebab-case · used as the branch + dir suffix">
        <Input
          value={slug}
          autoFocus
          onChange={(e) => setSlug(slugify(e.target.value))}
          placeholder="e.g. color-refactor"
          spellCheck={false}
        />
      </Field>
      {err && <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", marginTop: "var(--space-2)" }}>{err}</p>}
    </Modal>
  );
}

export default function AgentBuild() {
  const { slug = "" } = useParams();
  const qc = useQueryClient();
  // `err` is the BRIEF read's slot and nothing else now. The agent's own load
  // failure is the query's, and the two used to share this one variable.
  const [err, setErr] = useState<string | null>(null);

  const [pickedPath, setPickedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [fileDirty, setFileDirty] = useState(false);
  const [fileSaving, setFileSaving] = useState(false);

  const [paneTab, setPaneTab] = useState<PaneTab>("editor");

  // Chat-first: the Workbench (files/editor/preview/diff/prompt/notes) is a
  // secondary panel, closed by default so you land in pure chat. Persisted so it
  // stays where you left it. This single toggle is the ONLY view control on the
  // page — there is no separate chat expander.
  const [workbenchOpen, setWorkbenchOpen] = useState<boolean>(
    () => userStorage.local.getItem(WORKBENCH_KEY) === "open",
  );
  const toggleWorkbench = useCallback(() => {
    setWorkbenchOpen((cur) => {
      const next = !cur;
      userStorage.local.setItem(WORKBENCH_KEY, next ? "open" : "closed");
      return next;
    });
  }, []);

  // System prompt (file is PROJECT.md; the label is what's "system prompt"-y).
  const [systemPrompt, setSystemPrompt] = useState<string>("");
  const [systemPromptDirty, setSystemPromptDirty] = useState(false);

  // Interventions. Display-only here (the box below renders the text; the only
  // write is the POST that appends one), so the server's copy IS the value and
  // there is nothing local to keep in step with it.
  const [newIntervention, setNewIntervention] = useState("");

  const [confirmReset, setConfirmReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  // Diff tab state
  const [revertPath, setRevertPath] = useState<string | null>(null);
  const [reverting, setReverting] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [committing, setCommitting] = useState(false);
  const [commitResult, setCommitResult] = useState<string | null>(null);

  // Preview tab state
  const [customCmd, setCustomCmd] = useState("");
  const [previewPort, setPreviewPort] = useState<number>(8501);
  const [previewBusy, setPreviewBusy] = useState(false);
  // The log line a FAILED start writes. The running process's log is served and
  // read below; this is the one thing the server has no copy of.
  const [startFailure, setStartFailure] = useState("");

  // Branches. THE PIN IS THE AGENT'S, NOT THIS PAGE'S, and that is the whole
  // fix: this used to be a `useState` that nothing but the <Select> ever read,
  // so choosing a branch moved a variable and changed nothing about where the
  // agent's turns ran. It now renders `agent.branch_slug` and writes through
  // PATCH /api/agents/build/{slug}, which re-derives the agent's cwd.
  const [pinBusy, setPinBusy] = useState(false);
  const [pinErr, setPinErr] = useState<string | null>(null);
  const [newBranchOpen, setNewBranchOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameName, setRenameName] = useState("");
  const [renamePurpose, setRenamePurpose] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameErr, setRenameErr] = useState<string | null>(null);

  // THE AGENT AND ITS TREE, one entry. Two endpoints, but one question ("what
  // is this build agent and what files does it have"), asked together, always
  // rendered together, and invalidated by the same writes. The tree read is
  // allowed to fail on its own and answer an empty list, which is what the
  // `tR.ok ? ... : { tree: [] }` it replaces did; the AGENT read failing is the
  // whole page's error, which is why only that one throws.
  const buildQuery = useQuery({
    queryKey: queryKeys.agents.build(slug),
    queryFn: async (): Promise<{ agent: BuildAgent; tree: TreeNode[] }> => {
      const [aR, tR] = await Promise.all([
        fetch(`/api/agents/build/${slug}`),
        fetch(`/api/agents/build/${slug}/tree`),
      ]);
      if (!aR.ok) {
        throw new Error(`agent: ${shortHttpError(aR.status, aR.statusText, await aR.text().catch(() => ""))}`);
      }
      const tj = tR.ok ? await tR.json() : { tree: [] };
      // BuildAgentDetail, named from the generated client: this route's own
      // model, rather than the shared type re-asserted at the wire.
      const a = (await aR.json()) as components["schemas"]["BuildAgentDetail"] & BuildAgent;
      return { agent: a, tree: (tj.tree || []) as TreeNode[] };
    },
  });
  const agent = buildQuery.data?.agent ?? null;
  const tree = buildQuery.data?.tree ?? null;
  const loadErr = buildQuery.isError ? String(buildQuery.error) : null;

  useCrumbLabel(agent?.name || null);

  const openRename = useCallback(() => {
    if (!agent) return;
    setRenameName(agent.name);
    setRenamePurpose(agent.purpose || "");
    setRenameErr(null);
    setRenameOpen(true);
  }, [agent]);

  const submitRename = useCallback(async () => {
    if (!agent || !renameName.trim()) return;
    setRenameBusy(true);
    setRenameErr(null);
    try {
      const r = await fetch(`/api/agents/build/${agent.slug}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: renameName.trim(), purpose: renamePurpose.trim() }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const updated = (await r.json()) as components["schemas"]["BuildAgentDetail"] & BuildAgent;
      qc.setQueryData<{ agent: BuildAgent; tree: TreeNode[] }>(
        queryKeys.agents.build(agent.slug),
        (prev) => (prev ? { ...prev, agent: updated } : prev),
      );
      setRenameOpen(false);
    } catch (e) {
      setRenameErr(String(e));
    } finally {
      setRenameBusy(false);
    }
  }, [agent, renameName, renamePurpose, qc]);

  // Move the agent onto another branch, or back to the project's main tree.
  // Takes effect from the NEXT turn (a running turn's cwd was fixed when its
  // process started) and keeps the agent's Claude Code session: the
  // conversation is about the work, not about the directory.
  const pinBranch = useCallback(async (next: string) => {
    if (!slug) return;
    setPinBusy(true);
    setPinErr(null);
    try {
      const r = await fetch(`/api/agents/build/${slug}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch_slug: next }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const updated = (await r.json()) as components["schemas"]["BuildAgentDetail"] & BuildAgent;
      qc.setQueryData<{ agent: BuildAgent; tree: TreeNode[] }>(
        queryKeys.agents.build(slug),
        (prev) => (prev ? { ...prev, agent: updated } : prev),
      );
    } catch (e) {
      setPinErr(String(e));
    } finally {
      setPinBusy(false);
    }
  }, [slug, qc]);

  // THE STEERING DOCS. Interventions is display-only, so it is simply the
  // server's copy. The brief is NOT: it seeds an editor with a dirty flag, and
  // a background refetch landing on top of unsaved typing is the one thing this
  // page must not do, so it stays a one-shot read into local draft state.
  const interventionsQuery = useQuery({
    queryKey: queryKeys.agents.buildInterventions(slug),
    queryFn: async (): Promise<string> => {
      const r = await fetch(`/api/agents/build/${slug}/interventions`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return ((await r.json())?.content as string) || "";
    },
  });
  const interventions = interventionsQuery.data ?? "";

  const loadBrief = useCallback(async () => {
    try {
      const bR = await fetch(`/api/agents/build/${slug}/brief`);
      setSystemPrompt((bR.ok ? await bR.json() : { content: "" }).content || "");
      setSystemPromptDirty(false);
    } catch (e) {
      setErr(String(e));
    }
  }, [slug]);

  useEffect(() => { void loadBrief(); }, [loadBrief]);

  /** Re-read everything this page shows. What Reset session owed `loadAll`. */
  const reloadAll = useCallback(async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.agents.build(slug) }),
      qc.invalidateQueries({ queryKey: queryKeys.agents.buildInterventions(slug) }),
      loadBrief(),
    ]);
  }, [qc, slug, loadBrief]);

  // The trees this agent's project has on disk. Keyed by the PROJECT, not by
  // this agent: two makers on one project are looking at one list.
  const worktreesQuery = useQuery({
    queryKey: queryKeys.projects.worktrees(agent?.project_slug ?? ""),
    enabled: !!agent?.project_slug,
    queryFn: async (): Promise<Worktree[]> => {
      const r = await fetch(`/api/worktrees/${agent!.project_slug}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return ((await r.json()) || []) as Worktree[];
    },
  });
  // [] rather than null while there is no project to ask about, and on a failed
  // read: the picker renders "no branches" either way, which is what the old
  // `.catch(() => setWorktrees([]))` produced.
  const worktrees = agent?.project_slug
    ? (worktreesQuery.isPending ? null : (worktreesQuery.data ?? []))
    : [];

  const dropIntervention = useCallback(async () => {
    const text = newIntervention.trim();
    if (!text) return;
    setNewIntervention("");
    try {
      const r = await fetch(`/api/agents/build/${slug}/interventions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (r.ok) {
        await qc.invalidateQueries({ queryKey: queryKeys.agents.buildInterventions(slug) });
      }
    } catch { /* surface via reload */ }
  }, [newIntervention, slug, qc]);

  const pickFile = useCallback(async (path: string) => {
    setPickedPath(path);
    setPaneTab("editor");
    try {
      const r = await fetch(`/api/agents/build/${slug}/file?path=${encodeURIComponent(path)}`);
      if (!r.ok) {
        if (r.status === 415) setFileContent("(binary file - preview not supported)");
        else setFileContent(`(${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))})`);
        setFileDirty(false);
        return;
      }
      const j = await r.json();
      setFileContent(j.content || "");
      setFileDirty(false);
    } catch (e) {
      setFileContent(`(error: ${e})`);
    }
  }, [slug]);

  const saveFile = useCallback(async () => {
    if (!pickedPath || !fileDirty) return;
    setFileSaving(true);
    try {
      await fetch(`/api/agents/build/${slug}/file?path=${encodeURIComponent(pickedPath)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: fileContent }),
      });
      setFileDirty(false);
    } finally {
      setFileSaving(false);
    }
  }, [pickedPath, fileDirty, fileContent, slug]);

  const saveSystemPrompt = useCallback(async () => {
    await fetch(`/api/agents/build/${slug}/brief`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: systemPrompt }),
    });
    setSystemPromptDirty(false);
  }, [systemPrompt, slug]);

  const resetSession = useCallback(async () => {
    setResetting(true);
    try {
      await fetch(`/api/agents/build/${slug}/session/reset`, { method: "POST" });
      await reloadAll();
    } finally {
      setResetting(false);
      setConfirmReset(false);
    }
  }, [slug, reloadAll]);

  // READ WHEN THE TAB IS OPEN, which `enabled` states directly. It used to be an
  // effect watching `paneTab`, and the diff of a tree nobody is looking at is a
  // request nobody asked for.
  type DiffFile = { status: string; path: string; diff: string };
  const diffQuery = useQuery({
    queryKey: queryKeys.agents.buildDiff(slug),
    enabled: paneTab === "diff",
    queryFn: async (): Promise<{ files: DiffFile[]; summary: string }> => {
      const r = await fetch(`/api/agents/build/${slug}/diff`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      return { files: (j.files || []) as DiffFile[], summary: (j.summary as string) || "" };
    },
  });
  const diffFiles = diffQuery.data?.files ?? null;
  // "(error)" is what the old `r.ok ? ... : { summary: "(error)" }` put in this
  // slot, and it is still what a failed read says.
  const diffSummary = diffQuery.isError ? "(error)" : (diffQuery.data?.summary ?? "");
  const diffLoading = diffQuery.isFetching;
  const loadDiff = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.agents.buildDiff(slug) });
  }, [qc, slug]);

  const revertFile = useCallback(async () => {
    if (!revertPath) return;
    setReverting(true);
    try {
      await fetch(`/api/agents/build/${slug}/diff/revert-file`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: revertPath }),
      });
      await loadDiff();
      if (pickedPath === revertPath) await pickFile(revertPath);
    } finally {
      setReverting(false);
      setRevertPath(null);
    }
  }, [revertPath, slug, loadDiff, pickedPath, pickFile]);

  // THE PREVIEW PROCESS, on a cadence rather than a hand-rolled timer. This was
  // a raw `setInterval` around two fetches, which is the legacy mechanism ADR
  // 0025 retires and the frontend-state ratchet counts. `refetchInterval` is
  // the same 5 seconds, and it pauses with the tab instead of firing into a
  // hidden one.
  const previewQuery = useQuery({
    queryKey: queryKeys.agents.buildPreview(slug),
    refetchInterval: 5_000,
    queryFn: async (): Promise<{ status: PreviewStatus | null; log: string }> => {
      const [sR, lR] = await Promise.all([
        fetch(`/api/agents/build/${slug}/preview/status`),
        fetch(`/api/agents/build/${slug}/preview/log`),
      ]);
      const st: PreviewStatus | null = sR.ok ? await sR.json() : null;
      const l = lR.ok ? await lR.json() : { log: "" };
      return { status: st, log: (l.log as string) || "" };
    },
  });
  const preview = previewQuery.data?.status ?? null;
  // A failed start's message wins while it is set, because it is the newest
  // thing that happened and the server has no copy of it.
  const previewLog = startFailure || (previewQuery.data?.log ?? "");
  const loadPreview = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.agents.buildPreview(slug) });
  }, [qc, slug]);

  // The suggested port, adopted once the probe answers. Kept as an effect
  // because it seeds an INPUT the operator can then change; deriving it would
  // overwrite their typing on the next poll.
  useEffect(() => {
    if (!preview) return;
    if (preview.suggested_port && !previewPort) setPreviewPort(preview.suggested_port);
    if (!preview.running && preview.suggested_port && previewPort === 8501) {
      setPreviewPort(preview.suggested_port);
    }
  }, [preview, previewPort]);

  const startPreview = useCallback(async (commandOverride?: string) => {
    setPreviewBusy(true);
    try {
      const cmd = (commandOverride ?? customCmd ?? preview?.suggested?.command ?? "").trim();
      const r = await fetch(`/api/agents/build/${slug}/preview/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd, port: previewPort }),
      });
      if (!r.ok) {
        setStartFailure(`(start failed: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))})`);
      } else {
        setStartFailure("");
      }
      await loadPreview();
    } finally {
      setPreviewBusy(false);
    }
  }, [slug, customCmd, preview?.suggested?.command, previewPort, loadPreview]);

  const stopPreview = useCallback(async () => {
    setPreviewBusy(true);
    try {
      await fetch(`/api/agents/build/${slug}/preview/stop`, { method: "POST" });
      await loadPreview();
    } finally {
      setPreviewBusy(false);
    }
  }, [slug, loadPreview]);

  const commitAll = useCallback(async () => {
    setCommitting(true);
    setCommitResult(null);
    try {
      const r = await fetch(`/api/agents/build/${slug}/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: commitMessage || "Build agent checkpoint" }),
      });
      const j = r.ok ? await r.json() : { ok: false, reason: "request failed" };
      if (j.ok) {
        setCommitResult(`committed ${String(j.commit).slice(0, 8)}`);
        setCommitMessage("");
        await loadDiff();
      } else {
        setCommitResult(j.reason || "no changes");
      }
    } finally {
      setCommitting(false);
    }
  }, [slug, commitMessage, loadDiff]);

  if (loadErr) {
    return (
      <div>
        <PageHeader title="Build agent" />
        <StateBlock
          variant="error"
          title="Couldn't load agent"
          hint={<code>GET /api/agents/build/{slug} ({loadErr})</code>}
          action={<Button size="sm" onClick={() => { void reloadAll(); }}>Retry</Button>}
        />
      </div>
    );
  }
  if (!agent) {
    return (
      <div>
        <PageHeader title="Loading agent..." />
      </div>
    );
  }

  const previewRunning = !!preview?.running;
  const suggestedCmd = preview?.suggested?.command ?? "";
  const previewLabel = previewRunning
    ? `Preview · :${preview?.port}`
    : "Preview · stopped";
  // The file tree is only companion to the file-oriented panes; Preview / Prompt /
  // Notes take the full workbench width.
  const showFiles = paneTab === "editor" || paneTab === "diff";

  return (
    <div className="ws-agent-build-page">
      <PageHeader
        title={agent.name}
        sub={
          <span>
            <code className="ws-mono">{agent.slug}</code> · Build agent ·{" "}
            {agent.project_slug ? (
              <>linked to <Link to={`/projects/${agent.project_slug}`} style={{ color: "var(--accent)" }}>{agent.project_slug}</Link></>
            ) : (
              <span className="ws-muted">freestanding</span>
            )}
          </span>
        }
        actions={
          <Row wrap>
            <Button
              size="sm"
              variant={workbenchOpen ? "primary" : "ghost"}
              onClick={toggleWorkbench}
              aria-pressed={workbenchOpen}
              title={workbenchOpen ? "Hide the workbench (files · editor · preview · diff)" : "Show the workbench (files · editor · preview · diff)"}
            >
              <PanelsTopLeft aria-hidden /> {workbenchOpen ? "Hide workbench" : "Workbench"}
            </Button>
            <Button size="sm" variant="ghost" onClick={openRename}>
              <Pencil aria-hidden /> Rename
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { void reloadAll(); }}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
            {agent.claude_session && (
              <Button size="sm" variant="danger-ghost" onClick={() => setConfirmReset(true)} disabled={resetting}>
                <RotateCcw aria-hidden /> Reset session
              </Button>
            )}
          </Row>
        }
      />

      {/* --- Status bar (top) --- */}
      <Card style={{ padding: "var(--space-2) var(--space-3)", marginBottom: "var(--space-4)" }}>
        <Row wrap className="ws-agent-build__status">
          <StatusPill status={stateKind(agent.state)}>Agent {agentStateWord(agent.state)}</StatusPill>
          <StatusPill status={agent.session_alive ? "ok" : "stopped"}>
            {agent.session_alive ? "Session live" : "Session per-turn"}
          </StatusPill>
          {previewRunning && preview?.public_url ? (
            <a href={preview.public_url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              <StatusPill status="ok">{previewLabel} <ExternalLink size={11} aria-hidden /></StatusPill>
            </a>
          ) : (
            <StatusPill status={previewRunning ? "ok" : "stopped"}>{previewLabel}</StatusPill>
          )}
          {agent.project_slug && (worktrees && worktrees.length > 0) ? (
            <Row style={{ gap: "var(--space-2)" }}>
              <span className="ws-card__meta">Branch</span>
              <Select
                value={agent.branch_slug || ""}
                onChange={(e) => { void pinBranch(e.target.value); }}
                disabled={pinBusy}
                aria-label="Branch"
                title={`Turns run in ${agent.cwd || "this agent's directory"}. Changing this moves the next turn, not the one in flight.`}
              >
                {/* ONE NAME FOR THE TRUNK, EVERYWHERE. The chat toolbar's tree
                    pill calls it "Shared tree" and this used to call it "Main
                    branch", which is two names for one place in the two
                    controls that do the same job. It is also the more accurate
                    of the two: what matters about it is that everything else is
                    in it, not what git happens to call the ref. */}
                <option value="">Shared tree</option>
                {worktrees.map((w) => (
                  <option key={w.branch_slug} value={w.branch_slug}>{w.branch_slug}</option>
                ))}
              </Select>
              <Button size="sm" variant="ghost" onClick={() => setNewBranchOpen(true)} disabled={pinBusy}>
                <Plus aria-hidden /> New branch
              </Button>
            </Row>
          ) : agent.project_slug ? (
            <Row style={{ gap: "var(--space-2)" }}>
              <StatusPill status="stopped">Shared tree</StatusPill>
              <Button size="sm" variant="ghost" onClick={() => setNewBranchOpen(true)}>
                <Plus aria-hidden /> New branch
              </Button>
            </Row>
          ) : (
            <StatusPill status="stopped">No project</StatusPill>
          )}
        </Row>
        {pinErr && (
          <Notice tone="error" detail={pinErr}>
            Couldn't move this agent to that branch. It is still working in{" "}
            <code className="ws-mono">{agent.cwd}</code>.
          </Notice>
        )}
      </Card>

      <div className="ws-agent-chat" data-workbench={workbenchOpen ? "open" : "closed"}>
        {/* --- PRIMARY: chat (the surface you land on) --- */}
        <section className="ws-agent-chat__chat" aria-label="Chat">
          <Chat
            contextKey={`agent:${agent.slug}`}
            // This page IS the work pane, so it states the mode rather than
            // waiting for the roster fetch to say what it already knows.
            mode="build"
            variant="panel"
            placeholder="Tell the agent what to build..."
            scoped
            hideExpand
          />
        </section>

        {/* --- SECONDARY: workbench (files · editor · preview · diff · prompt · notes) --- */}
        {workbenchOpen && (
        <section className="ws-agent-chat__workbench" aria-label="Workbench">
          <div className="ws-agent-chat__wb-head">
            <span className="ws-region-head__title"><PanelsTopLeft size={14} aria-hidden /> Workbench</span>
            <Button
              size="sm"
              variant="ghost"
              icon
              onClick={toggleWorkbench}
              aria-label="Close workbench"
              title="Close workbench"
            >
              <X aria-hidden />
            </Button>
          </div>
          <div className="ws-agent-chat__wb-tabs">
            <Segmented
              options={PANE_VIEWS}
              value={paneTab}
              onChange={setPaneTab}
              ariaLabel="Workbench view"
            />
          </div>
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-2) 0" }}>
            {PANE_HELP[paneTab]}
          </p>
          <div className="ws-agent-chat__wb-body" data-files={showFiles ? "on" : "off"}>
            {showFiles && (
              <Card className="ws-agent-chat__files" style={{ padding: "var(--space-3)" }}>
                <span className="ws-region-head__title" style={{ marginBottom: "var(--space-2)" }}>
                  <FolderTree size={14} aria-hidden /> Files
                </span>
                <p className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", marginBottom: "0.5rem" }}>
                  {agent.cwd}
                </p>
                <div className="ws-agent-chat__files-body">
                  {!tree ? (
                    <p className="ws-muted">...</p>
                  ) : tree.length === 0 ? (
                    <p className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>(empty)</p>
                  ) : (
                    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                      {tree.map((n) => (
                        <FileTreeNode key={n.path} node={n} depth={0} onPick={pickFile} picked={pickedPath} />
                      ))}
                    </ul>
                  )}
                </div>
              </Card>
            )}
          <div className="ws-agent-build__center-view">
            {paneTab === "editor" && (
              <Card style={{ padding: "var(--space-3)" }}>
                {!pickedPath ? (
                  <StateBlock title="Pick a file" hint="Select a file from the tree to edit it." />
                ) : (
                  <>
                    <Row between style={{ marginBottom: "0.5rem" }}>
                      <span className="ws-mono ws-card__meta">{pickedPath}{fileDirty ? " · modified" : ""}</span>
                      <Button size="sm" variant="primary" onClick={saveFile} disabled={!fileDirty} loading={fileSaving}>
                        <Save aria-hidden /> Save
                      </Button>
                    </Row>
                    <Textarea
                      value={fileContent}
                      onChange={(e) => { setFileContent(e.target.value); setFileDirty(true); }}
                      className="ws-agent-build__editor-area"
                      rows={22}
                      style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
                    />
                  </>
                )}
              </Card>
            )}
            {paneTab === "preview" && (
              <Card style={{ padding: "var(--space-3)" }}>
                <Row between style={{ marginBottom: "var(--space-3)" }}>
                  <StatusPill status={previewRunning ? "running" : "stopped"}>
                    {previewRunning ? `running · pid ${preview?.pid} · :${preview?.port}` : "stopped"}
                  </StatusPill>
                  {previewRunning && preview?.public_url && (
                    <a href={preview.public_url} target="_blank" rel="noreferrer" className="ws-agent-build__preview-open">
                      <Button variant="primary">
                        <ExternalLink aria-hidden /> Open preview
                      </Button>
                    </a>
                  )}
                </Row>

                {!previewRunning ? (
                  <>
                    {suggestedCmd && (
                      <div style={{ marginBottom: "var(--space-3)" }}>
                        <span className="ws-card__meta" style={{ display: "block", marginBottom: "0.3rem" }}>
                          Suggested command
                        </span>
                        <Row>
                          <code
                            className="ws-mono"
                            style={{
                              flex: 1, padding: "0.4rem 0.6rem",
                              background: "var(--bg-inset)", borderRadius: "var(--radius-sm)",
                              fontSize: "var(--fs-sm)", overflow: "auto", whiteSpace: "nowrap",
                            }}
                          >
                            {suggestedCmd}
                          </code>
                          <Button variant="ghost" onClick={() => setCustomCmd(suggestedCmd)}>
                            Use suggested
                          </Button>
                        </Row>
                      </div>
                    )}
                    <Row style={{ marginBottom: "var(--space-3)" }}>
                      <Field label="Port" inline>
                        <Input
                          type="number"
                          min={8500}
                          max={8599}
                          value={previewPort}
                          onChange={(e) => setPreviewPort(Number(e.target.value) || 8501)}
                          style={{ width: "6rem" }}
                        />
                      </Field>
                      <Button
                        variant="primary"
                        onClick={() => startPreview()}
                        loading={previewBusy}
                        disabled={!(customCmd.trim() || suggestedCmd)}
                      >
                        <Play aria-hidden /> Boot preview
                      </Button>
                    </Row>
                    <details style={{ marginBottom: "var(--space-3)" }}>
                      <summary className="ws-card__meta" style={{ cursor: "pointer" }}>
                        Custom command
                      </summary>
                      <div style={{ marginTop: "var(--space-2)" }}>
                        <Input
                          placeholder="dev command (e.g. npm run dev -- --port $WORKSHOP_DEV_PORT)"
                          value={customCmd}
                          onChange={(e) => setCustomCmd(e.target.value)}
                          style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
                        />
                        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "0.3rem" }}>
                          <code className="ws-mono">$WORKSHOP_DEV_PORT</code> is injected into the env.
                        </p>
                      </div>
                    </details>
                  </>
                ) : (
                  <>
                    <Row between style={{ marginBottom: "var(--space-3)" }}>
                      <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{preview?.command}</span>
                      <Button variant="danger-ghost" onClick={stopPreview} loading={previewBusy}>
                        <Square aria-hidden /> Stop
                      </Button>
                    </Row>
                    <iframe
                      title="preview"
                      src={preview?.public_url || `http://localhost:${preview?.port}/`}
                      style={{ width: "100%", flex: 1, minHeight: "55vh", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}
                    />
                  </>
                )}

                {previewLog && (
                  <details style={{ marginTop: "var(--space-3)" }}>
                    <summary className="ws-card__meta" style={{ cursor: "pointer" }}>dev-server log (last 8KB)</summary>
                    <pre style={{
                      padding: "0.5rem", background: "var(--bg-inset)",
                      borderRadius: "var(--radius-sm)", fontSize: "var(--fs-xs)",
                      maxHeight: "20vh", overflow: "auto", marginTop: "0.4rem",
                      whiteSpace: "pre-wrap",
                    }}>{previewLog}</pre>
                  </details>
                )}
              </Card>
            )}
            {paneTab === "diff" && (
              <Card style={{ padding: "var(--space-3)" }}>
                <Row between style={{ marginBottom: "0.5rem" }}>
                  <span className="ws-card__meta ws-mono">{diffSummary || "no changes"}</span>
                  <Button size="sm" variant="ghost" onClick={loadDiff} disabled={diffLoading}>
                    <RefreshCw aria-hidden /> Refresh
                  </Button>
                </Row>
                <Row style={{ marginBottom: "var(--space-3)" }}>
                  <Input
                    style={{ flex: 1 }}
                    placeholder="commit message (default: 'Build agent checkpoint')"
                    value={commitMessage}
                    onChange={(e) => setCommitMessage(e.target.value)}
                  />
                  <Button variant="primary" onClick={commitAll} loading={committing}
                    disabled={!diffFiles || diffFiles.length === 0}>
                    <GitCommit aria-hidden /> Commit all
                  </Button>
                </Row>
                {commitResult && (
                  <p className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", marginBottom: "0.5rem" }}>
                    {commitResult}
                  </p>
                )}
                {diffLoading ? (
                  <p className="ws-muted">Loading diff...</p>
                ) : !diffFiles ? (
                  <p className="ws-muted">...</p>
                ) : diffFiles.length === 0 ? (
                  <StateBlock title="Working tree is clean" hint="No diffs vs HEAD." />
                ) : (
                  diffFiles.map((f) => {
                    const lab = statusLabel(f.status);
                    return (
                      <div key={f.path} style={{ marginBottom: "var(--space-4)" }}>
                        <Row between style={{ marginBottom: "0.3rem" }}>
                          <span>
                            <StatusPill status={lab.tone === "err" ? "danger" : lab.tone === "warn" ? "warn" : lab.tone === "ok" ? "ok" : "stopped"}>
                              {lab.text}
                            </StatusPill>
                            <span className="ws-mono" style={{ marginLeft: "0.5rem", fontSize: "var(--fs-sm)" }}>{f.path}</span>
                          </span>
                          <Button
                            size="sm"
                            variant="danger-ghost"
                            onClick={() => setRevertPath(f.path)}
                            disabled={reverting}
                          >
                            <Trash2 aria-hidden /> Revert
                          </Button>
                        </Row>
                        <DiffBlock diff={f.diff} />
                      </div>
                    );
                  })
                )}
              </Card>
            )}
            {paneTab === "prompt" && (
              <Card style={{ padding: "var(--space-3)" }}>
                {err && (
                  <Notice tone="error" detail={err} where={`/api/agents/build/${slug}/brief`}>
                    Couldn't read the system prompt. The box below is empty, not blank.
                  </Notice>
                )}
                <Row between style={{ marginBottom: "0.5rem" }}>
                  <span className="ws-card__meta">System prompt, read first every turn.</span>
                  <Button size="sm" variant="primary" onClick={saveSystemPrompt} disabled={!systemPromptDirty}>
                    <Save aria-hidden /> Save
                  </Button>
                </Row>
                <Textarea
                  value={systemPrompt}
                  onChange={(e) => { setSystemPrompt(e.target.value); setSystemPromptDirty(true); }}
                  className="ws-agent-build__editor-area"
                  rows={16}
                  style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
                />
                <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "0.4rem" }}>
                  Stored as <code className="ws-mono">PROJECT.md</code>.
                </p>
              </Card>
            )}
            {paneTab === "notes" && (
              <Card style={{ padding: "var(--space-3)" }}>
                <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>
                  Drop a note for the agent to pick up on its next turn. The agent reads{" "}
                  <code className="ws-mono">INTERVENTIONS.md</code> between turns; unacked entries are injected into context.
                </p>
                <Row style={{ marginBottom: "var(--space-3)" }}>
                  <Input
                    value={newIntervention}
                    placeholder="Drop intervention..."
                    onChange={(e) => setNewIntervention(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); dropIntervention(); } }}
                    style={{ flex: 1 }}
                  />
                  <Button variant="primary" onClick={dropIntervention} disabled={!newIntervention.trim()}>
                    Drop
                  </Button>
                </Row>
                <pre
                  style={{
                    padding: "0.6rem", background: "var(--bg-inset)",
                    borderRadius: "var(--radius-sm)", fontSize: "var(--fs-xs)",
                    flex: 1, minHeight: "12rem", overflow: "auto", whiteSpace: "pre-wrap",
                  }}
                >
                  {interventions || "(no interventions yet)"}
                </pre>
              </Card>
            )}
          </div>
          </div>
        </section>
        )}
      </div>

      <ConfirmDialog
        open={confirmReset}
        onCancel={() => setConfirmReset(false)}
        onConfirm={resetSession}
        title="Reset session?"
        body="The next message starts a fresh session, and the agent won't remember prior turns. Chat history here is unaffected."
        confirmLabel="Reset session"
        busy={resetting}
      />

      <ConfirmDialog
        open={revertPath !== null}
        onCancel={() => setRevertPath(null)}
        onConfirm={revertFile}
        title="Revert file?"
        body={
          <span>
            Restore <code className="ws-mono">{revertPath}</code> to its HEAD version.
            Untracked files are deleted. This can't be undone.
          </span>
        }
        confirmLabel="Revert"
        busy={reverting}
      />

      {agent.project_slug && (
        <NewBranchModal
          open={newBranchOpen}
          projectSlug={agent.project_slug}
          onCancel={() => setNewBranchOpen(false)}
          onCreated={(s) => {
            setNewBranchOpen(false);
            // Cutting a branch from here is a request to WORK in it, so the
            // agent moves onto it. Creating one and leaving the agent where it
            // was is the shape that made this control feel inert.
            void pinBranch(s);
            // The list gained a branch.
            if (agent.project_slug) {
              void qc.invalidateQueries({
                queryKey: queryKeys.projects.worktrees(agent.project_slug),
              });
            }
          }}
        />
      )}

      <Modal open={renameOpen} onClose={() => setRenameOpen(false)} title="Rename agent">
        <Stack>
          <p className="ws-muted">
            The name and purpose are what you see across Workshop. Its id{" "}
            (<code className="ws-mono">{agent.slug}</code>) does not change.
          </p>
          <Field label="Name" required>
            <Input value={renameName} onChange={(e) => setRenameName(e.target.value)} autoFocus />
          </Field>
          <Field label="Purpose" help="One sentence: what this is and who it is for.">
            <Textarea
              value={renamePurpose}
              onChange={(e) => setRenamePurpose(e.target.value)}
              rows={2}
            />
          </Field>
          {renameErr && (
            <Notice tone="error" detail={renameErr}>Couldn't rename this agent.</Notice>
          )}
          <Row end>
            <Button variant="ghost" onClick={() => setRenameOpen(false)} disabled={renameBusy}>Cancel</Button>
            <Button variant="primary" onClick={submitRename} loading={renameBusy} disabled={!renameName.trim()}>
              Save
            </Button>
          </Row>
        </Stack>
      </Modal>
    </div>
  );
}
