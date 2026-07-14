import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, ExternalLink, RefreshCw, Pencil, X } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusDot, Badge, Skeleton, Field, Input,
} from "../ui";

// Environment / secrets panel for a project. The app manifest declares env
// REQUIREMENTS (key + metadata, no values); the operator fills VALUES here; the
// backend secrets_store holds them (gitignored, 0600) and the runner injects
// them at spawn. Values are write-only: this panel only ever learns which keys
// are SET, never their values (the check_secrets semantics) — so secrets show a
// "Set" pill + a Replace input, never a readable value.

type Requirement = {
  key: string;
  description: string;
  required: boolean;
  secret: boolean;
  example: string;
  how_to_obtain: string;
  service: string;
  group: string;
  set: boolean;
};
type EnvOverview = {
  slug: string;
  self?: boolean;
  requirements: Requirement[];
  undeclared_set: string[];
  missing_required: string[];
};

function HowTo({ text }: { text: string }) {
  if (!text) return null;
  // The manifest flattens how_to_obtain to "steps — url"; linkify a trailing URL.
  const m = text.match(/(https?:\/\/\S+)/);
  if (!m) return <span className="ws-field__help">{text}</span>;
  const url = m[1];
  const rest = text.replace(url, "").replace(/[—-]\s*$/, "").trim();
  return (
    <span className="ws-field__help">
      {rest ? rest + " " : ""}
      <a href={url} target="_blank" rel="noreferrer" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        {url.replace(/^https?:\/\//, "").slice(0, 48)}<ExternalLink size={12} aria-hidden />
      </a>
    </span>
  );
}

export default function EnvironmentPanel({ slug }: { slug: string }) {
  const [data, setData] = useState<EnvOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/env`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [slug]);
  useEffect(() => { load(); }, [load]);

  const save = useCallback(async () => {
    // Only send keys the operator actually typed a value for; blanks are ignored
    // (a blank does NOT clear — clearing is the explicit per-key Clear button).
    const values: Record<string, string> = {};
    for (const [k, v] of Object.entries(draft)) if (v.trim()) values[k] = v;
    if (Object.keys(values).length === 0) { setEditing(false); setDraft({}); return; }
    setSaving(true);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/env`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ values }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
      setDraft({}); setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, slug]);

  const clearKey = useCallback(async (key: string) => {
    setSaving(true);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/env/${encodeURIComponent(key)}`, { method: "DELETE" });
      if (r.ok) setData(await r.json());
    } finally {
      setSaving(false);
    }
  }, [slug]);

  // Group requirements by `group` (ungrouped first), preserving manifest order.
  const groups = useMemo(() => {
    const reqs = data?.requirements ?? [];
    const order: string[] = [];
    const by: Record<string, Requirement[]> = {};
    for (const r of reqs) {
      const g = r.group || "";
      if (!(g in by)) { by[g] = []; order.push(g); }
      by[g].push(r);
    }
    return order.map((g) => ({ group: g, items: by[g] }));
  }, [data]);

  const missingCount = data?.missing_required?.length ?? 0;

  // The self / platform project has no deployable-app env registry (its secrets
  // live in the operator's own config) — don't show this panel for it.
  if (data?.self) return null;

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<KeyRound aria-hidden />}>Environment</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {missingCount > 0 && <StatusDot status="warn">{missingCount} required missing</StatusDot>}
          <Button size="sm" variant="ghost" onClick={load}><RefreshCw aria-hidden /> Refresh</Button>
          {!editing ? (
            <Button size="sm" variant="primary" onClick={() => setEditing(true)} disabled={loading || !!err}>
              <Pencil aria-hidden /> Edit values
            </Button>
          ) : (
            <>
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft({}); }}>
                <X aria-hidden /> Cancel
              </Button>
              <Button size="sm" variant="primary" loading={saving} disabled={saving} onClick={save}>
                Save
              </Button>
            </>
          )}
        </Row>
      </Row>

      <Card>
        {loading ? (
          <Stack gap="sm"><Skeleton width="55%" /><Skeleton width="40%" /></Stack>
        ) : err ? (
          <StateBlock variant="error" title="Couldn't load environment"
            hint={<code>GET /api/base-apps/run/{slug}/env ({err})</code>}
            action={<Button size="sm" onClick={load}>Retry</Button>} />
        ) : (data?.requirements.length ?? 0) === 0 ? (
          <StateBlock
            title="No environment variables declared"
            hint="Declare them in workshop.app.json `env_requirements` (the builder can add them): key, description, required, secret, how_to_obtain. They'll appear here to fill." />
        ) : (
          <Stack>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              Values are stored outside git and injected into the app at start. Secrets are write-only,
              shown only as Set or Missing. A required value left unset blocks that service from starting.
            </p>
            {groups.map(({ group, items }) => (
              <div key={group || "_ungrouped"}>
                {group && <span className="ws-card__meta">{group}</span>}
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {items.map((r) => (
                    <li key={r.key} style={{ padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)" }}>
                      <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                        <StatusDot status={r.set ? "ok" : r.required ? "warn" : "stopped"}>
                          {r.set ? "set" : r.required ? "missing" : "unset"}
                        </StatusDot>
                        <span className="ws-mono" style={{ fontWeight: "var(--fw-semi)" }}>{r.key}</span>
                        {r.required && <Badge>required</Badge>}
                        {!r.secret && <Badge>config</Badge>}
                        {r.service && r.service !== "*" && (
                          <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{r.service}</span>
                        )}
                        {editing && r.set && (
                          <>
                            <span style={{ flex: "1 1 1rem" }} />
                            <Button size="sm" variant="danger-ghost" loading={saving} onClick={() => clearKey(r.key)}>
                              Clear
                            </Button>
                          </>
                        )}
                      </Row>
                      {r.description && (
                        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--space-1) 0 0" }}>
                          {r.description}
                        </p>
                      )}
                      <HowTo text={r.how_to_obtain} />
                      {editing && (
                        <div style={{ marginTop: "var(--space-2)" }}>
                          <Field>
                            <Input
                              type={r.secret ? "password" : "text"}
                              autoComplete="off"
                              placeholder={r.set ? "Replace value (leave blank to keep)" : r.example || "Set value"}
                              value={draft[r.key] ?? ""}
                              onChange={(e) => setDraft((d) => ({ ...d, [r.key]: e.target.value }))}
                            />
                          </Field>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {(data?.undeclared_set?.length ?? 0) > 0 && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Also set (not declared in the manifest): {data!.undeclared_set.join(", ")}.
              </p>
            )}
          </Stack>
        )}
      </Card>
    </section>
  );
}
