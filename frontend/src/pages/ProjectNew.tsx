import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MessagesSquare } from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  Markdown,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Skeleton,
  StateBlock,
  Textarea,
  type SegmentedOption,
} from "../ui";
import type { ProjectSensitivity } from "@shared/types";
import { slugify } from "../components/chat/uiCardActions";

// New-project flow. Single form. Submits to POST /api/projects/ which
// scaffolds from the chosen template, writes the brief into PROJECT.md,
// auto-git-inits, registers, and returns the project. Templates render purely
// from GET /api/projects/templates: the backend is the single source of
// truth for which templates exist and what they're labeled/hinted.

type TemplateInfo = { slug: string; label: string; hint: string };

const SENS_OPTIONS: SegmentedOption<ProjectSensitivity>[] = [
  { value: "private", label: "Private" },
  { value: "shareable", label: "Shareable" },
  { value: "public", label: "Public" },
];

// Best-effort preview of the PROJECT.md the backend will write, mirroring
// _scaffold_blank / _scaffold_from_template in backend/routes/projects.py:
// "blank" lets a supplied brief stand on its own and falls back to a
// Goal/Constraints/Open-questions skeleton when empty; real templates keep
// their own PROJECT.md scaffold and merge the brief under a "## Brief" section.
function previewProjectMd(template: string, title: string, purpose: string, brief: string): string {
  const heading = `# ${title.trim() || "Untitled project"}\n\n> ${purpose.trim() || "(one-sentence purpose)"}\n\n`;
  if (template === "blank") {
    const b = brief.trim();
    return b
      ? `${heading}${b}\n`
      : `${heading}## Goal\n\n## Constraints\n\n- (fill in)\n\n## Open questions\n\n- (fill in)\n`;
  }
  return `${heading}(this template's own PROJECT.md scaffold)\n\n## Brief\n\n${brief.trim() || "(brief goes here)"}\n`;
}

export default function ProjectNew() {
  const nav = useNavigate();
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [purpose, setPurpose] = useState("");
  const [template, setTemplate] = useState<string>("blank");
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [templatesErr, setTemplatesErr] = useState<string | null>(null);
  const [sensitivity, setSensitivity] = useState<ProjectSensitivity>("shareable");
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [slugTouched, setSlugTouched] = useState(false);

  const effectiveSlug = useMemo(
    () => (slugTouched ? slug : slugify(title)),
    [slug, slugTouched, title],
  );

  const loadTemplates = useCallback(() => {
    setTemplates(null);
    setTemplatesErr(null);
    fetch("/api/projects/templates")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then((items: TemplateInfo[]) => {
        setTemplates(items.length ? items : [{ slug: "blank", label: "Blank", hint: "" }]);
      })
      .catch((e) => {
        setTemplatesErr(String(e));
        setTemplates([]);
      });
  }, []);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const selectedTemplate = useMemo(
    () => templates?.find((t) => t.slug === template) ?? null,
    [templates, template],
  );

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/projects/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: effectiveSlug,
          title: title.trim() || effectiveSlug,
          purpose: purpose.trim(),
          template,
          sensitivity,
          brief: brief.trim(),
        }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const p = await r.json();
      nav(`/projects/${p.slug}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = !!effectiveSlug && !!title.trim() && !busy;

  return (
    <div style={{ maxWidth: 1040 }}>
      <PageHeader title="New project" sub="Scaffold, git-init, and open it" />

      <Row wrap style={{ marginBottom: "var(--space-4)" }}>
        <Link to="/chat/new-project" style={{ textDecoration: "none" }}>
          <Button variant="ghost" size="sm">
            <MessagesSquare aria-hidden /> Prefer to talk it through first? Build with chat
          </Button>
        </Link>
      </Row>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-5)", alignItems: "flex-start" }}>
        <Card style={{ padding: "var(--space-5)", flex: "1 1 420px", minWidth: 0 }}>
          <Field label="Title" required>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="What are you building?"
              autoFocus
            />
          </Field>
          <Field
            label="Slug"
            help="kebab-case · used as the directory name + route segment"
          >
            <Input
              value={effectiveSlug}
              onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
              placeholder={slugify(title) || "my-project"}
              spellCheck={false}
            />
          </Field>
          <Field label="Purpose" help="Becomes the manifest purpose + first line of PROJECT.md.">
            <Input
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              placeholder="One sentence: what this is and who it is for"
            />
          </Field>
          <Field label="Template">
            {templates === null ? (
              <Skeleton width="60%" />
            ) : templatesErr ? (
              <StateBlock
                variant="error"
                title="Couldn't load templates"
                hint={<code>GET /api/projects/templates: {templatesErr}</code>}
                action={<Button size="sm" onClick={loadTemplates}>Retry</Button>}
              />
            ) : (
              <Select
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
              >
                {templates.map((t) => (
                  <option key={t.slug} value={t.slug}>
                    {t.label}{t.hint ? ` · ${t.hint}` : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Sensitivity">
            <Segmented
              options={SENS_OPTIONS}
              value={sensitivity}
              onChange={setSensitivity}
              ariaLabel="Sensitivity"
            />
          </Field>
          <Field
            label="Initial brief"
            help="Goes into PROJECT.md as the agent's source of truth. Goals, constraints, open questions."
          >
            <Textarea
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              rows={8}
              placeholder={
                "Goal:\n- ...\n\nConstraints:\n- ...\n\nOpen questions:\n- ..."
              }
            />
          </Field>
          {err && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <StateBlock variant="error" title="Couldn't create project" hint={<code>{err}</code>} />
            </div>
          )}
          <div style={{ marginTop: "var(--space-4)", display: "flex", gap: "var(--space-3)", justifyContent: "flex-end" }}>
            <Button variant="ghost" onClick={() => nav("/projects")} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={!canSubmit} loading={busy}>
              Create project
            </Button>
          </div>
        </Card>

        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <SectionLabel>Preview</SectionLabel>
          <Card style={{ padding: "var(--space-5)" }}>
            {selectedTemplate?.hint && (
              <p className="ws-muted" style={{ marginTop: 0 }}>{selectedTemplate.hint}</p>
            )}
            <span className="ws-card__meta ws-mono" style={{ display: "block", margin: "var(--space-3) 0 var(--space-2)" }}>
              PROJECT.md
            </span>
            <Markdown>{previewProjectMd(template, title, purpose, brief)}</Markdown>
          </Card>
        </div>
      </div>
    </div>
  );
}
