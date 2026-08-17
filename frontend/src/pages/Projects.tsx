import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, Plus, FolderGit2 } from "lucide-react";
import type { Project, ProjectSensitivity, ProjectState } from "@shared/types";
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
  StateBlock,
  StatusPill,
  type SegmentedOption,
  type StatusKind,
  Notice,
} from "@workshop/ui";
import { timeAgo } from "../lib/time";
import { shortHttpError } from "../lib/httpError";

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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
        Clones the repo and adds it to Workshop. Accepts https, ssh, or a path on disk.
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
      <Field label="Slug" help="lowercase with dashes · taken from the URL, change it if you like">
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
        <Notice tone="error" detail={err}>Couldn't import that project.</Notice>
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

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    fetch("/api/projects/")
      .then(async (r) => {
        if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        return r.json();
      })
      .then(setProjects)
      .catch((e) => setErr(String(e)));
  }, []);

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
            <Button variant="ghost" size="sm" onClick={() => setImportOpen(true)}>
              <Download aria-hidden /> Import from git
            </Button>
          </Row>
        }
      />

      {!projects ? (
        <p className="ws-muted">Loading…</p>
      ) : projects.length === 0 ? (
        <StateBlock
          title="No projects yet"
          hint={
            <span>
              Projects are standalone things you build: sites, sub-apps, capability
              providers. Start one with{" "}
              <Link to="/projects/new" style={{ color: "var(--accent)" }}>+ New project</Link>.
            </span>
          }
        />
      ) : (
        <CardGrid className="ws-projects-grid">
          {projects.map((p) => (
            <li key={p.slug}>
              <Link to={`/projects/${p.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive className="ws-project-card">
                  <Row between>
                    <h2 className="ws-card__title">
                      <FolderGit2 size={16} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {p.title}
                    </h2>
                    <StatusPill status={stateKind(p.state)}>{p.state}</StatusPill>
                  </Row>
                  <p className="ws-card__meta ws-mono">{p.slug}</p>
                  <p className="ws-project-card__desc" style={{ marginTop: "var(--space-2)" }}>
                    {p.purpose || " "}
                  </p>
                  <p className="ws-card__meta ws-project-card__when">
                    touched {timeAgo(p.last_touched)} · {p.sensitivity}
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
