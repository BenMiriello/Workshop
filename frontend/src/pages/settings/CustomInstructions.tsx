import { useEffect, useMemo, useState } from "react";
import { FileText, ShieldCheck } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  Field,
  PageHeader,
  Row,
  SectionLabel,
  Select,
  Textarea,
  StateBlock,
  StatusDot,
  Skeleton,
} from "@workshop/ui";
import { scopeLabel } from "../../lib/scope";
import { shortHttpError } from "../../lib/httpError";

// Settings / Custom instructions: the operator's L3 layer.
//
// The system prompt has four layers (see
// docs/platform-redesign/agent-context-and-toolkit-2026-08-20.md): L0 platform
// kernel and L1 orientation and L2 persona are platform-owned and NOT editable
// here; this page edits ONLY L3, the operator's own free-text instructions per
// scope, which sit below the platform layers and never override them.
//
// A prompt is config, so it commits on explicit Save (ActionBar), not on change.

type Provider = { key: string; label?: string };

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

export default function CustomInstructions() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [existing, setExisting] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<string>("general");
  const [text, setText] = useState<string>("");
  const [baseline, setBaseline] = useState<string>("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);

  const loadIndex = async () => {
    setLoadErr(null);
    try {
      const [provs, all] = await Promise.all([
        fetch("/api/chat/providers").then((r) => j<Provider[]>(r)),
        fetch("/api/chat/custom-instructions/all").then((r) => j<{ scopes: Record<string, string> }>(r)),
      ]);
      setProviders(Array.isArray(provs) ? provs : []);
      setExisting(all.scopes || {});
    } catch (e) {
      setLoadErr(String(e));
    }
  };

  const loadScope = async (key: string) => {
    setErr(null);
    try {
      const d = await fetch(`/api/chat/custom-instructions?scope=${encodeURIComponent(key)}`).then((r) =>
        j<{ scope: string; text: string }>(r),
      );
      setText(d.text || "");
      setBaseline(d.text || "");
    } catch (e) {
      setErr(`Couldn't load instructions for this scope: ${String(e)}`);
    }
  };

  useEffect(() => {
    loadIndex();
  }, []);

  useEffect(() => {
    if (providers) loadScope(scope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, providers]);

  const dirty = text !== baseline;

  // Scope options: every data provider (labelled by its scope), so the operator
  // picks a real scope rather than typing a key.
  const options = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; label: string }[] = [];
    for (const p of providers || []) {
      if (seen.has(p.key)) continue;
      seen.add(p.key);
      out.push({ value: p.key, label: scopeLabel(p.key) });
    }
    return out;
  }, [providers]);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const d = await fetch("/api/chat/custom-instructions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, text }),
      }).then((r) => j<{ scope: string; text: string }>(r));
      setBaseline(d.text || "");
      setText(d.text || "");
      setExisting((cur) => {
        const next = { ...cur };
        if (d.text) next[d.scope] = d.text;
        else delete next[d.scope];
        return next;
      });
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
    } catch (e) {
      setErr(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => setText(baseline);

  if (loadErr && !providers) {
    return (
      <div>
        <PageHeader title="Custom instructions" />
        <StateBlock
          variant="error"
          title="Couldn't load"
          hint={<code>/api/chat/providers</code>}
          action={<Button onClick={loadIndex}>Retry</Button>}
        />
      </div>
    );
  }
  if (!providers) {
    return (
      <div>
        <PageHeader title="Custom instructions" />
        <Card>
          <Skeleton width="40%" />
          <Skeleton width="70%" style={{ marginTop: "var(--space-3)" }} />
          <Skeleton width="55%" style={{ marginTop: "var(--space-2)" }} />
        </Card>
      </div>
    );
  }

  const setScopes = Object.keys(existing);

  return (
    <div>
      <PageHeader
        title="Custom instructions"
        sub="Your own instructions for a scope's chats. They sit below the platform's own instructions and never override them."
      />

      <Card flat style={{ marginBottom: "var(--space-6)" }}>
        <Row style={{ alignItems: "flex-start", gap: "var(--space-3)" }}>
          <ShieldCheck size={16} strokeWidth={1.8} aria-hidden style={{ marginTop: 2, color: "var(--accent)", flexShrink: 0 }} />
          <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
            The assistant's built-in instructions are platform owned and cannot be edited or read here. What you
            write below is added on top, for the chosen scope only, and applies from that scope's next turn.
          </span>
        </Row>
      </Card>

      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<FileText size={14} strokeWidth={1.8} aria-hidden />}>Edit a scope</SectionLabel>
        <Card>
          <Field label="Scope" htmlFor="ci-scope">
            <Select id="ci-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <div style={{ marginTop: "var(--space-4)" }}>
            <Field label="Instructions" htmlFor="ci-text">
              <Textarea
                id="ci-text"
                rows={10}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="e.g. When answering in this scope, prefer short bullet lists and always cite the file you changed."
              />
            </Field>
          </div>
          {err && (
            <p className="ws-field__error" style={{ marginTop: "var(--space-3)" }}>
              {err}
            </p>
          )}
        </Card>
      </section>

      {setScopes.length > 0 && (
        <section style={{ marginBottom: "var(--space-6)" }}>
          <SectionLabel>Scopes with instructions set</SectionLabel>
          <Card>
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
              {setScopes.map((s) => (
                <StatusDot key={s} status="ok">
                  {scopeLabel(s)}
                </StatusDot>
              ))}
            </Row>
          </Card>
        </section>
      )}

      <ActionBar flash={flash ? "Saved" : dirty ? "unsaved changes" : undefined}>
        <Button variant="primary" onClick={save} loading={saving} disabled={!dirty}>
          Save
        </Button>
        <Button variant="ghost" type="button" onClick={cancel} disabled={!dirty || saving}>
          Cancel
        </Button>
      </ActionBar>
    </div>
  );
}
