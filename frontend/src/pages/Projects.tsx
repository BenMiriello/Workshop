import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, MessagesSquare, Plus, FolderGit2 } from "lucide-react";
import type { BuildAgent, Project, ProjectSensitivity, ProjectState } from "@shared/types";
import { createBlankBuilder } from "../lib/blankBuilder";
import {
  Button,
  Card,
  CardGrid,
  Field,
  Input,
  Modal,
  PageHeader,
  Row,
  Segmented,
  SectionLabel,
  StateBlock,
  StatusPill,
  type SegmentedOption,
  type StatusKind,
} from "../ui";
// Relative "when" for the blank-builder list. Compact, no dependency.
function whenLabel(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(t).toLocaleDateString();
}

const SENS_OPTIONS: SegmentedOption<ProjectSensitivity>[] = [
  { value: "private", label: "Private" },
  { value: "shareable", label: "Shareable" },
  { value: "public", label: "Public" },
];

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

function deriveSlugFromUrl(url: string): string {
  const trimmed = url.trim().replace(/\.git$/i, "");
  if (!trimmed) return "";
  const parts = trimmed.split(/[/:]/).filter(Boolean);
  return slugify(parts[parts.length - 1] || "");
}

function ImportFromGitModal({
  open, onCancel, onImported,
}: { open: boolean; onCancel: () => void; onImported: (slug: string) => void }) {
  const [url, setUrl] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [title, setTitle] = useState("");
  const [sensitivity, setSensitivity] = useState<ProjectSensitivity>("shareable");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const effectiveSlug = useMemo(
    () => (slugTouched ? slug : deriveSlugFromUrl(url)),
    [slug, slugTouched, url],
  );

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/projects/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url.trim(),
          slug: effectiveSlug,
          title: title.trim(),
          sensitivity,
        }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const p = await r.json();
      onImported(p.slug || effectiveSlug);
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
      title="Import from git"
      actions={
        <>
          <Button onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={!url.trim() || !effectiveSlug || busy} loading={busy}>
            Import
          </Button>
        </>
      }
    >
      <p className="ws-muted" style={{ marginBottom: "var(--space-3)" }}>
        Clones the repo under <code className="ws-mono">~/workshop/projects/&lt;slug&gt;</code>
        {" "}and registers it. Accepts https, ssh, or a path on disk.
      </p>
      <Field label="URL" required>
        <Input
          value={url}
          autoFocus
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/you/repo.git"
          spellCheck={false}
        />
      </Field>
      <Field label="Slug" help="kebab-case · directory name. Auto-derived from the URL basename.">
        <Input
          value={effectiveSlug}
          onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
          placeholder={deriveSlugFromUrl(url) || "my-project"}
          spellCheck={false}
        />
      </Field>
      <Field label="Title" help="Defaults to the slug if blank.">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={effectiveSlug} />
      </Field>
      <Field label="Sensitivity">
        <Segmented options={SENS_OPTIONS} value={sensitivity} onChange={setSensitivity} ariaLabel="Sensitivity" />
      </Field>
      {err && (
        <div style={{ marginTop: "var(--space-3)" }}>
          <StateBlock variant="error" title="Couldn't import" hint={<code>{err}</code>} />
        </div>
      )}
    </Modal>
  );
}

// Projects hub — operator-built things. Distinct from Apps (tools the operator
// uses) and Agents (Letta workers). Each Project has its own code, manifest,
// state, and persistent vibe-coding session. See ADR-008 + projects-hub spec.

const stateKind = (s: ProjectState): StatusKind => {
  switch (s) {
    case "sketch": return "warn";
    case "wip": return "running";
    case "mounted":
    case "shared":
    case "published":
      return "ok";
    case "parked":
    case "retired":
      return "stopped";
    default:
      return "warn";
  }
};

const fmtAge = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  // Freestanding "Blank project N" builders — the "build with chat" flow spawns a
  // real builder that lives permanently in the Agents list. Surfaced here too so a
  // half-shaped blank project is one tap away from the Projects page it belongs to.
  const [blankBuilders, setBlankBuilders] = useState<BuildAgent[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    fetch("/api/projects/")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then(setProjects)
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    fetch("/api/agents/build")
      .then((r) => (r.ok ? r.json() : []))
      .then((all: BuildAgent[]) =>
        setBlankBuilders(
          all.filter((a) => !a.project_slug && a.state !== "retired" && /^blank-project-\d+$/.test(a.slug)),
        ),
      )
      .catch(() => { /* non-essential — the button still spawns a fresh builder */ });
  }, []);

  const startWithChat = useCallback(async () => {
    setChatBusy(true);
    try {
      const a = await createBlankBuilder();
      nav(`/chat/${encodeURIComponent(`build:${a.slug}`)}`);
    } catch {
      setChatBusy(false);
    }
  }, [nav]);

  if (err) {
    return (
      <div>
        <PageHeader title="Projects" />
        <StateBlock variant="error" title="Couldn't load projects" hint={<code>/api/projects/</code>} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Projects"
        sub={projects ? `${projects.length} project${projects.length === 1 ? "" : "s"}` : undefined}
        actions={
          <Row wrap>
            <Link to="/projects/new">
              <Button variant="primary" size="sm">
                <Plus aria-hidden /> New project
              </Button>
            </Link>
            <Button variant="ghost" size="sm" onClick={startWithChat} loading={chatBusy}>
              <MessagesSquare aria-hidden /> Build with chat
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setImportOpen(true)}>
              <Download aria-hidden /> Import from git
            </Button>
          </Row>
        }
      />

      {blankBuilders.length > 0 && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <SectionLabel>Blank project builders</SectionLabel>
          <CardGrid>
            {blankBuilders.map((a) => (
              <Link
                key={a.slug}
                to={`/chat/${encodeURIComponent(`build:${a.slug}`)}`}
                style={{ textDecoration: "none" }}
              >
                <Card interactive style={{ padding: "var(--space-3)" }}>
                  <Row between style={{ gap: "var(--space-2)" }}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
                      <MessagesSquare size={15} aria-hidden />
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {a.name}
                      </span>
                    </span>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", flex: "0 0 auto" }}>
                      {whenLabel(a.last_active || a.created)}
                    </span>
                  </Row>
                </Card>
              </Link>
            ))}
          </CardGrid>
        </div>
      )}

      {!projects ? (
        <p className="ws-muted">Loading…</p>
      ) : projects.length === 0 ? (
        <StateBlock
          title="No projects yet"
          hint={
            <span>
              Projects are standalone things you build — sites, sub-apps, capability
              providers. Start one with{" "}
              <Link to="/projects/new" style={{ color: "var(--accent)" }}>+ New project</Link>.
            </span>
          }
        />
      ) : (
        <CardGrid>
          {projects.map((p) => (
            <li key={p.slug}>
              <Link to={`/projects/${p.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between>
                    <h2 className="ws-card__title">
                      <FolderGit2 size={16} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {p.title}
                    </h2>
                    <StatusPill status={stateKind(p.state)}>{p.state}</StatusPill>
                  </Row>
                  <p className="ws-card__meta ws-mono">{p.slug}</p>
                  {p.purpose && <p style={{ marginTop: "var(--space-2)" }}>{p.purpose}</p>}
                  <p className="ws-card__meta" style={{ marginTop: "var(--space-2)" }}>
                    touched {fmtAge(p.last_touched)} · {p.sensitivity}
                  </p>
                </Card>
              </Link>
            </li>
          ))}
        </CardGrid>
      )}

      <ImportFromGitModal
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onImported={(s) => {
          setImportOpen(false);
          nav(`/projects/${s}`);
        }}
      />
    </div>
  );
}
