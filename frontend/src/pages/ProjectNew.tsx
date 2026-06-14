import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  Field,
  Input,
  PageHeader,
  Segmented,
  Select,
  StateBlock,
  Textarea,
  type SegmentedOption,
} from "../ui";
import type { ProjectSensitivity } from "@shared/types";

// New-project flow. Single form. Submits to POST /api/projects/ which
// scaffolds from the chosen template, writes the brief into PROJECT.md,
// auto-git-inits, registers, and returns the project.

type TemplateInfo = { slug: string; label: string; hint: string };

const TEMPLATE_HINTS: Record<string, string> = {
  "blank": "Empty scaffold with PROJECT.md, TODO.md, AGENTS.md, .claude/",
  "static-site": "Single-page HTML/CSS + light-dark color-scheme",
  "integrated-sub-app": "Mounts under /apps/<slug> in workshop",
  "standalone-sub-app": "Same package, own origin via TS Serve",
  "fastapi-capability-provider": "Backend service declaring provides:[…]",
  "agent-tool": "Letta tool registration",
  "data-import-and-view": "Ingest routine + viewer page",
};

const TEMPLATE_LABELS: Record<string, string> = {
  "blank": "Blank",
  "static-site": "Static site",
  "integrated-sub-app": "Integrated sub-app",
  "standalone-sub-app": "Standalone sub-app",
  "fastapi-capability-provider": "FastAPI provider",
  "agent-tool": "Agent tool",
  "data-import-and-view": "Data import + view",
};

const SENS_OPTIONS: SegmentedOption<ProjectSensitivity>[] = [
  { value: "private", label: "Private" },
  { value: "shareable", label: "Shareable" },
  { value: "public", label: "Public" },
];

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

export default function ProjectNew() {
  const nav = useNavigate();
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [purpose, setPurpose] = useState("");
  const [template, setTemplate] = useState<string>("blank");
  const [templates, setTemplates] = useState<TemplateInfo[]>([
    { slug: "blank", label: "Blank", hint: TEMPLATE_HINTS["blank"] || "" },
  ]);
  const [sensitivity, setSensitivity] = useState<ProjectSensitivity>("shareable");
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [slugTouched, setSlugTouched] = useState(false);

  const effectiveSlug = useMemo(
    () => (slugTouched ? slug : slugify(title)),
    [slug, slugTouched, title],
  );

  useEffect(() => {
    fetch("/api/projects/templates")
      .then((r) => (r.ok ? r.json() : []))
      .then((items: { slug: string; label?: string; hint?: string }[]) => {
        const mapped = items.map((t) => ({
          slug: t.slug,
          label: TEMPLATE_LABELS[t.slug] || t.label || t.slug,
          hint: TEMPLATE_HINTS[t.slug] || t.hint || "",
        }));
        if (mapped.length) setTemplates(mapped);
      })
      .catch(() => { /* keep default blank */ });
  }, []);

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
    <div style={{ maxWidth: 760 }}>
      <PageHeader title="New project" sub="Scaffold + auto-git + open vibe session" />
      <Card style={{ padding: "var(--space-5)" }}>
        <Field label="Title" required>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Noetic landing page"
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
        <Field label="Purpose" help="One sentence. Becomes the manifest purpose + first line of PROJECT.md.">
          <Input
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="Public marketing site for Noetic"
          />
        </Field>
        <Field label="Template">
          <Select
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          >
            {templates.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.label}{t.hint ? ` — ${t.hint}` : ""}
              </option>
            ))}
          </Select>
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
            Scaffold project
          </Button>
        </div>
      </Card>
    </div>
  );
}
