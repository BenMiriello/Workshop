import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  File, Folder, FolderOpen, GitCommit, Play, RefreshCw, RotateCcw, Save, Square,
  Trash2, ExternalLink, Plus, Maximize2, Minimize2, FileCode, MonitorPlay,
  GitCompare, MessageSquare, FolderTree,
} from "lucide-react";
import type { ReactNode } from "react";
import type { BuildAgent } from "@shared/types";
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
  StateBlock,
  StatusPill,
  Tabs,
  Textarea,
  type SegmentedOption,
  type StatusKind,
  type TabItem,
} from "../ui";
import Chat from "../components/Chat";
import { useCrumbLabel } from "../components/appNav";

// Build-agent detail = the in-web coding UI. Per ADR-009:
//   - Top status strip with pills (state / preview / branch) and a
//     pop-chat link.
//   - Two docs tabs: system-prompt | interventions.
//   - Preview is a link-out-of-box card; custom-command behind a disclosure.
//   - Branches picker on the status bar; modal opens for a new branch.
//   - Existing file tree + editor + diff tabs preserved.

type TreeNode = {
  name: string;
  path: string;
  kind: "dir" | "file";
  size?: number | null;
  children?: TreeNode[];
};

type PaneTab = "editor" | "preview" | "diff";
type DocTab = "system-prompt" | "interventions";

// Which region is expanded full-width (IDE-style focus). "all" = the 3-up layout.
type FocusRegion = "all" | "files" | "center" | "chat";
const FOCUS_KEY = "ws-agent-build-focus";

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

// Editor/Preview/Diff is a view switch (view preference) — a Segmented control
// reads more clearly as "pick a view" than tab underlines, and keeps one height.
const PANE_VIEWS: SegmentedOption<PaneTab>[] = [
  { value: "editor", label: <><FileCode size={15} aria-hidden /> Editor</> },
  { value: "preview", label: <><MonitorPlay size={15} aria-hidden /> Preview</> },
  { value: "diff", label: <><GitCompare size={15} aria-hidden /> Diff</> },
];

const PANE_HELP: Record<PaneTab, string> = {
  editor: "The file selected in Files.",
  preview: "The live running app (dev server).",
  diff: "Working-tree changes vs HEAD, with commit.",
};

const DOC_TABS: TabItem<DocTab>[] = [
  { value: "system-prompt", label: "System prompt" },
  { value: "interventions", label: "Interventions" },
];

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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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

// Region header: title + a focus/restore toggle. When this region is focused it
// shows "restore" (Minimize2); otherwise "expand" (Maximize2). One control height.
function RegionHead({
  icon, title, region, focus, onFocus, extra,
}: {
  icon: ReactNode;
  title: string;
  region: Exclude<FocusRegion, "all">;
  focus: FocusRegion;
  onFocus: (next: FocusRegion) => void;
  extra?: ReactNode;
}) {
  const focused = focus === region;
  return (
    <div className="ws-region-head">
      <span className="ws-region-head__title">{icon} {title}</span>
      <Row style={{ gap: "var(--space-1)" }}>
        {extra}
        <Button
          size="sm"
          variant="ghost"
          icon
          onClick={() => onFocus(focused ? "all" : region)}
          aria-label={focused ? `Restore ${title} to split view` : `Expand ${title} full width`}
          title={focused ? "Restore split view" : "Expand full width"}
        >
          {focused ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
        </Button>
      </Row>
    </div>
  );
}

export default function AgentBuild() {
  const { slug = "" } = useParams();
  const [agent, setAgent] = useState<BuildAgent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeNode[] | null>(null);

  const [pickedPath, setPickedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [fileDirty, setFileDirty] = useState(false);
  const [fileSaving, setFileSaving] = useState(false);

  const [paneTab, setPaneTab] = useState<PaneTab>("editor");
  const [docTab, setDocTab] = useState<DocTab>("system-prompt");

  // IDE-style pane focus, persisted across reloads.
  const [focus, setFocus] = useState<FocusRegion>(() => {
    if (typeof localStorage === "undefined") return "all";
    const v = localStorage.getItem(FOCUS_KEY) as FocusRegion | null;
    return v === "files" || v === "center" || v === "chat" || v === "all" ? v : "all";
  });
  const changeFocus = useCallback((next: FocusRegion) => {
    setFocus(next);
    try { localStorage.setItem(FOCUS_KEY, next); } catch { /* ignore */ }
  }, []);

  // System prompt (file is PROJECT.md; the label is what's "system prompt"-y).
  const [systemPrompt, setSystemPrompt] = useState<string>("");
  const [systemPromptDirty, setSystemPromptDirty] = useState(false);

  // Interventions
  const [interventions, setInterventions] = useState<string>("");
  const [newIntervention, setNewIntervention] = useState("");

  const [confirmReset, setConfirmReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  // Diff tab state
  const [diffSummary, setDiffSummary] = useState<string>("");
  const [diffFiles, setDiffFiles] = useState<{ status: string; path: string; diff: string }[] | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [revertPath, setRevertPath] = useState<string | null>(null);
  const [reverting, setReverting] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [committing, setCommitting] = useState(false);
  const [commitResult, setCommitResult] = useState<string | null>(null);

  // Preview tab state
  const [preview, setPreview] = useState<PreviewStatus | null>(null);
  const [customCmd, setCustomCmd] = useState("");
  const [previewPort, setPreviewPort] = useState<number>(8501);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewLog, setPreviewLog] = useState("");

  // Branches
  const [worktrees, setWorktrees] = useState<Worktree[] | null>(null);
  const [branchSlug, setBranchSlug] = useState<string>("");
  const [newBranchOpen, setNewBranchOpen] = useState(false);

  useCrumbLabel(agent?.name || null);

  const loadAll = useCallback(async () => {
    try {
      const [aR, tR, bR, iR] = await Promise.all([
        fetch(`/api/agents/build/${slug}`),
        fetch(`/api/agents/build/${slug}/tree`),
        fetch(`/api/agents/build/${slug}/brief`),
        fetch(`/api/agents/build/${slug}/interventions`),
      ]);
      if (!aR.ok) throw new Error(`agent: ${aR.status} ${await aR.text()}`);
      setAgent(await aR.json());
      const tj = tR.ok ? await tR.json() : { tree: [] };
      setTree(tj.tree || []);
      setSystemPrompt((bR.ok ? await bR.json() : { content: "" }).content || "");
      setSystemPromptDirty(false);
      setInterventions((iR.ok ? await iR.json() : { content: "" }).content || "");
    } catch (e) {
      setErr(String(e));
    }
  }, [slug]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Load worktrees once we know the project slug.
  useEffect(() => {
    if (!agent?.project_slug) {
      setWorktrees([]);
      return;
    }
    let cancelled = false;
    fetch(`/api/worktrees/${agent.project_slug}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list: Worktree[]) => { if (!cancelled) setWorktrees(list || []); })
      .catch(() => { if (!cancelled) setWorktrees([]); });
    return () => { cancelled = true; };
  }, [agent?.project_slug]);

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
        const iR = await fetch(`/api/agents/build/${slug}/interventions`);
        if (iR.ok) setInterventions((await iR.json()).content || "");
      }
    } catch { /* surface via reload */ }
  }, [newIntervention, slug]);

  const pickFile = useCallback(async (path: string) => {
    setPickedPath(path);
    setPaneTab("editor");
    // If Files is focused full-width, picking a file should reveal the editor.
    setFocus((f) => (f === "files" ? "all" : f));
    try {
      const r = await fetch(`/api/agents/build/${slug}/file?path=${encodeURIComponent(path)}`);
      if (!r.ok) {
        if (r.status === 415) setFileContent("(binary file - preview not supported)");
        else setFileContent(`(${r.status} ${await r.text()})`);
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
      await loadAll();
    } finally {
      setResetting(false);
      setConfirmReset(false);
    }
  }, [slug, loadAll]);

  const loadDiff = useCallback(async () => {
    setDiffLoading(true);
    try {
      const r = await fetch(`/api/agents/build/${slug}/diff`);
      const j = r.ok ? await r.json() : { files: [], summary: "(error)" };
      setDiffFiles(j.files || []);
      setDiffSummary(j.summary || "");
    } finally {
      setDiffLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    if (paneTab === "diff") loadDiff();
  }, [paneTab, loadDiff]);

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

  const loadPreview = useCallback(async () => {
    try {
      const [sR, lR] = await Promise.all([
        fetch(`/api/agents/build/${slug}/preview/status`),
        fetch(`/api/agents/build/${slug}/preview/log`),
      ]);
      const s: PreviewStatus | null = sR.ok ? await sR.json() : null;
      const l = lR.ok ? await lR.json() : { log: "" };
      setPreview(s);
      setPreviewLog(l.log || "");
      if (s) {
        if (s.suggested_port && !previewPort) setPreviewPort(s.suggested_port);
        if (!s.running && s.suggested_port && previewPort === 8501) setPreviewPort(s.suggested_port);
      }
    } catch { /* ignore */ }
  }, [slug, previewPort]);

  useEffect(() => {
    loadPreview();
    const t = setInterval(loadPreview, 5_000);
    return () => clearInterval(t);
  }, [loadPreview]);

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
        const t = await r.text();
        setPreviewLog(`(start failed: ${t})`);
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

  if (err) {
    return (
      <div>
        <PageHeader title="Build agent" />
        <StateBlock variant="error" title="Couldn't load agent" hint={<code>{err}</code>} />
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

  return (
    <div>
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
          <Row>
            <Button size="sm" variant="ghost" onClick={loadAll}>
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
          <StatusPill status={stateKind(agent.state)}>{agent.state}</StatusPill>
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
                value={branchSlug}
                onChange={(e) => setBranchSlug(e.target.value)}
                aria-label="Branch"
              >
                <option value="">Main branch</option>
                {worktrees.map((w) => (
                  <option key={w.branch_slug} value={w.branch_slug}>{w.branch_slug}</option>
                ))}
              </Select>
              <Button size="sm" variant="ghost" onClick={() => setNewBranchOpen(true)}>
                <Plus aria-hidden /> New branch
              </Button>
            </Row>
          ) : agent.project_slug ? (
            <Row style={{ gap: "var(--space-2)" }}>
              <StatusPill status="stopped">Main branch</StatusPill>
              <Button size="sm" variant="ghost" onClick={() => setNewBranchOpen(true)}>
                <Plus aria-hidden /> New branch
              </Button>
            </Row>
          ) : (
            <StatusPill status="stopped">No project</StatusPill>
          )}
        </Row>
      </Card>

      <div className="ws-agent-build" data-focus={focus}>
        {/* --- Files tree --- */}
        <Card className="ws-agent-build__files ws-agent-build__region" style={{ padding: "var(--space-3)" }}>
          <RegionHead
            icon={<FolderTree size={14} aria-hidden />}
            title="Files"
            region="files"
            focus={focus}
            onFocus={changeFocus}
          />
          <p className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", marginBottom: "0.5rem" }}>
            {agent.cwd}
          </p>
          <div className="ws-agent-build__region-body">
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

        {/* --- Center region: Editor / Preview / Diff --- */}
        <div className="ws-agent-build__main ws-agent-build__region">
          <RegionHead
            icon={<FileCode size={14} aria-hidden />}
            title="Center"
            region="center"
            focus={focus}
            onFocus={changeFocus}
            extra={
              <Segmented
                options={PANE_VIEWS}
                value={paneTab}
                onChange={setPaneTab}
                ariaLabel="Center view"
              />
            }
          />
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginBottom: "var(--space-2)" }}>
            {PANE_HELP[paneTab]}
          </p>
          <div className="ws-agent-build__region-body">
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
                    <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "0.5rem" }}>
                      Phase B will replace this with CodeMirror 6 (syntax highlight + multi-cursor).
                    </p>
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
          </div>

          {/* --- Docs tabs: system-prompt | interventions --- */}
          <div style={{ marginTop: "var(--space-5)", flex: "0 0 auto" }}>
            <Tabs items={DOC_TABS} value={docTab} onChange={setDocTab} />
            <Card style={{ padding: "var(--space-3)", marginTop: "var(--space-3)" }}>
              {docTab === "system-prompt" && (
                <>
                  <Row between style={{ marginBottom: "0.5rem" }}>
                    <span className="ws-card__meta">System prompt - the agent reads this first every turn.</span>
                    <Button size="sm" variant="primary" onClick={saveSystemPrompt} disabled={!systemPromptDirty}>
                      <Save aria-hidden /> Save
                    </Button>
                  </Row>
                  <Textarea
                    value={systemPrompt}
                    onChange={(e) => { setSystemPrompt(e.target.value); setSystemPromptDirty(true); }}
                    rows={12}
                    style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
                  />
                  <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "0.4rem" }}>
                    Treat this like the system prompt. Stored as <code className="ws-mono">PROJECT.md</code>.
                  </p>
                </>
              )}
              {docTab === "interventions" && (
                <>
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
                      maxHeight: "30vh", overflow: "auto", whiteSpace: "pre-wrap",
                    }}
                  >
                    {interventions || "(no interventions yet)"}
                  </pre>
                </>
              )}
            </Card>
          </div>
          </div>
        </div>

        {/* --- Chat (Build mode) --- */}
        <Card className="ws-agent-build__chat ws-agent-build__region" style={{ padding: "var(--space-3)" }}>
          <RegionHead
            icon={<MessageSquare size={14} aria-hidden />}
            title="Chat"
            region="chat"
            focus={focus}
            onFocus={changeFocus}
            extra={
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                {agent.claude_session ? "session live" : "per-turn"}
              </span>
            }
          />
          <div className="ws-agent-build__region-body" style={{ display: "flex", flexDirection: "column" }}>
            <Chat
              contextKey={`build:${agent.slug}`}
              mode="build"
              variant="panel"
              placeholder="Tell the agent what to build..."
              scoped
            />
          </div>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmReset}
        onCancel={() => setConfirmReset(false)}
        onConfirm={resetSession}
        title="Reset session?"
        body="Detaches this agent from its current Claude Code session. The next message starts a fresh session - the agent won't remember prior turns. Useful when the conversation gets confused. (Chat history in the UI is unaffected.)"
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
            setBranchSlug(s);
            // Refresh worktrees list
            fetch(`/api/worktrees/${agent.project_slug}`)
              .then((r) => (r.ok ? r.json() : []))
              .then((list: Worktree[]) => setWorktrees(list || []))
              .catch(() => { /* ignore */ });
          }}
        />
      )}
    </div>
  );
}
