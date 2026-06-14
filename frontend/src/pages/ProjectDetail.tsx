import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { Bot, GitBranch, Plus, RefreshCw, Save } from "lucide-react";
import type { BuildAgent, Project } from "@shared/types";
import {
  Button,
  Card,
  CardGrid,
  ConfirmDialog,
  Modal,
  PageHeader,
  Row,
  StateBlock,
  StatusPill,
  Tabs,
  Textarea,
  Input,
  Field,
  type StatusKind,
  type TabItem,
} from "../ui";
import { useCrumbLabel } from "../components/appNav";

type Worktree = {
  branch_slug: string;
  branch: string;
  dir: string;
};

// Project detail = the project's hub. NOT the coding UI — that lives under
// Agents (AgentBuild). This page shows:
//   - Title / purpose / state / sensitivity
//   - The brief (PROJECT.md), TODO (TODO.md), interventions log (read-only)
//   - The list of Build agents working on this project, with a "+ Spawn build
//     agent" CTA that hops you straight into the coding UI
//   - The surfaces summary (integrated/standalone/public) — wiring lives in
//     Phase C (gated on app-platform Phase 3)
// See decisions/ADR-008 + docs/projects-hub.md.

type DocTab = "brief" | "todo" | "interventions";

const DOC_TABS: TabItem<DocTab>[] = [
  { value: "brief", label: "Brief" },
  { value: "todo", label: "TODO" },
  { value: "interventions", label: "Interventions" },
];

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

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

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
      <div style={{ padding: "var(--space-4)" }}>
        <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
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
        {err && <p style={{ color: "var(--err)", fontSize: "var(--fs-sm)", marginTop: "var(--space-2)" }}>{err}</p>}
        <div style={{ marginTop: "var(--space-4)", display: "flex", gap: "var(--space-3)", justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy || !effectiveSlug || !name.trim()} loading={busy}>
            Spawn
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function ProjectDetail() {
  const { slug = "" } = useParams();
  const nav = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [docTab, setDocTab] = useState<DocTab>("brief");
  const [brief, setBrief] = useState("");
  const [briefDirty, setBriefDirty] = useState(false);
  const [todo, setTodo] = useState("");
  const [todoDirty, setTodoDirty] = useState(false);
  const [interventions, setInterventions] = useState("");
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

  useCrumbLabel(project?.title || null);

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

  useEffect(() => { loadWorktrees(); }, [loadWorktrees]);

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

  const loadAll = useCallback(async () => {
    try {
      const [pR, bR, tdR, iR, aR] = await Promise.all([
        fetch(`/api/projects/${slug}`),
        fetch(`/api/projects/${slug}/brief`),
        fetch(`/api/projects/${slug}/todo`),
        fetch(`/api/projects/${slug}/interventions`),
        fetch(`/api/agents/build`),
      ]);
      if (!pR.ok) throw new Error(`project: ${pR.status} ${await pR.text()}`);
      setProject(await pR.json());
      setBrief((bR.ok ? await bR.json() : { content: "" }).content || "");
      setBriefDirty(false);
      setTodo((tdR.ok ? await tdR.json() : { content: "" }).content || "");
      setTodoDirty(false);
      setInterventions((iR.ok ? await iR.json() : { content: "" }).content || "");
      const all: BuildAgent[] = aR.ok ? await aR.json() : [];
      setAgents(all.filter((a) => a.project_slug === slug));
    } catch (e) {
      setErr(String(e));
    }
  }, [slug]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const saveBrief = useCallback(async () => {
    await fetch(`/api/projects/${slug}/brief`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: brief }),
    });
    setBriefDirty(false);
  }, [brief, slug]);

  const saveTodo = useCallback(async () => {
    await fetch(`/api/projects/${slug}/todo`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: todo }),
    });
    setTodoDirty(false);
  }, [todo, slug]);

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

  return (
    <div>
      <PageHeader
        title={project.title}
        sub={<span><code className="ws-mono">{project.slug}</code> · {project.purpose}</span>}
        actions={
          <Row>
            <StatusPill status={stateKind(project.state)}>{project.state}</StatusPill>
            <Button size="sm" variant="ghost" onClick={loadAll}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </Row>
        }
      />

      {/* --- Build agents on this project --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <h2 className="ws-section__title" style={{ margin: 0 }}>Build agents</h2>
          <Button variant="primary" size="sm" onClick={() => setSpawning(true)}>
            <Plus aria-hidden /> Spawn build agent
          </Button>
        </Row>
        {!agents ? (
          <p className="ws-muted">…</p>
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

      {/* --- Brief / TODO / Interventions --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <h2 className="ws-section__title">Working brain</h2>
        <Tabs items={DOC_TABS} value={docTab} onChange={setDocTab} />
        <Card style={{ padding: "var(--space-3)", marginTop: "var(--space-3)" }}>
          {docTab === "brief" && (
            <>
              <Row between style={{ marginBottom: "0.5rem" }}>
                <span className="ws-card__meta">PROJECT.md · build agents read this every turn</span>
                <Button size="sm" variant="primary" onClick={saveBrief} disabled={!briefDirty}>
                  <Save aria-hidden /> Save
                </Button>
              </Row>
              <Textarea
                value={brief}
                onChange={(e) => { setBrief(e.target.value); setBriefDirty(true); }}
                rows={14}
                style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
              />
            </>
          )}
          {docTab === "todo" && (
            <>
              <Row between style={{ marginBottom: "0.5rem" }}>
                <span className="ws-card__meta">TODO.md · workshop checklist conventions apply</span>
                <Button size="sm" variant="primary" onClick={saveTodo} disabled={!todoDirty}>
                  <Save aria-hidden /> Save
                </Button>
              </Row>
              <Textarea
                value={todo}
                onChange={(e) => { setTodo(e.target.value); setTodoDirty(true); }}
                rows={12}
                style={{ fontFamily: "ui-monospace, monospace", fontSize: "var(--fs-sm)" }}
              />
            </>
          )}
          {docTab === "interventions" && (
            <pre
              style={{
                padding: "0.6rem", background: "var(--surface-2)",
                borderRadius: "var(--radius-sm)", fontSize: "var(--fs-xs)",
                maxHeight: "30vh", overflow: "auto", whiteSpace: "pre-wrap",
              }}
            >
              {interventions || "(no interventions yet)"}
            </pre>
          )}
        </Card>
      </section>

      {/* --- Branches (git worktrees) --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <h2 className="ws-section__title" style={{ margin: 0 }}>Branches</h2>
          <Button size="sm" variant="ghost" onClick={loadWorktrees} disabled={worktreesLoading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </Row>
        <Card style={{ padding: "var(--space-3)" }}>
          {worktreesLoading && !worktrees ? (
            <p className="ws-muted">Loading branches...</p>
          ) : worktreesErr ? (
            <StateBlock variant="error" title="Couldn't load branches" hint={<code>{worktreesErr}</code>} />
          ) : !worktrees || worktrees.length === 0 ? (
            <StateBlock
              title="No branches yet"
              hint="Work on a branched copy without touching main. Merge back when ready."
            />
          ) : worktrees.length > 50 ? (
            <StateBlock title="Too many branches" hint="Merge or discard old ones to keep this list readable." />
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {worktrees.map((w) => (
                <li key={w.branch_slug} style={{ borderBottom: "1px solid var(--border)", padding: "var(--space-2) 0" }}>
                  <Row wrap>
                    <GitBranch size={14} aria-hidden />
                    <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{w.branch_slug}</span>
                    <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{w.branch}</span>
                    <span className="ws-spacer" />
                    <Link
                      to={`/projects/${slug}/diff?branch=${encodeURIComponent(w.branch_slug)}`}
                      style={{ color: "var(--accent)", fontSize: "var(--fs-sm)" }}
                    >
                      Diff vs main
                    </Link>
                    <Button size="sm" variant="primary" onClick={() => { setMergeTarget(w); setMergeMessage(""); }}>
                      Merge
                    </Button>
                    <Button size="sm" variant="danger-ghost" onClick={() => setDiscardTarget(w)}>
                      Discard
                    </Button>
                  </Row>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* --- Surfaces summary --- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <h2 className="ws-section__title">Surfaces</h2>
        <Card style={{ padding: "var(--space-3)" }}>
          <p className="ws-muted ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
            integrated: {project.surfaces.integrated ?? "—"} ·
            standalone: {project.surfaces.standalone ? `:${project.surfaces.standalone.port}` : "—"} ·
            public: {project.surfaces.public ?? "—"}
          </p>
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "0.5rem" }}>
            One-click mount + guest tokens land in Phase C (app-platform manifests + grants).
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
    </div>
  );
}
