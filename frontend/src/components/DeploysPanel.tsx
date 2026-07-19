import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Rocket, RefreshCw, Plus, ExternalLink, RotateCcw, Trash2, ChevronDown, ChevronRight,
  TriangleAlert,
} from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Tag, Skeleton, Field, Input, Select, Modal, ConfirmDialog, type StatusKind,
} from "../ui";

// Deploys panel — per-project target bindings.
//
// A Deploy binds this project to a hosted target (e.g. Cloudflare Pages). Each
// deploy attempt is a Job (kind="deploy") — attempts are not stored here,
// only the target binding and the "what's currently live" pointer. Rows show
// the live version, the last-run status, and the affordances to kick a new
// run or roll back to a prior one.

type Deploy = {
  id: string;
  project_slug: string;
  name: string;
  target: { kind: string; project_name?: string; account_id?: string };
  source: {
    kind: string;
    branch?: string;
    build?: { cmd?: string; output_dir?: string; timeout_sec?: number };
  };
  domain?: string;
  autonomy: string;
  created_at: string;
  updated_at: string;
  last_run_id?: string;
  current?: { run_id: string; commit_sha?: string; deployed_at?: string; url?: string; upload_ref?: string };
};

type Run = {
  id: string;
  kind?: string;
  trigger?: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  binding?: { deploy_id?: string; ref?: string };
  evidence?: { summary?: string; url?: string; commit_sha?: string; log_tail?: string; missing_keys?: string[] };
};

type ProviderCatalog = {
  kind: string;
  label: string;
  env_requirements: { key: string; required: boolean; description: string }[];
  target_fields: { key: string; required: boolean; description: string }[];
  source_kinds: string[];
};

const JOB_KIND: Record<string, StatusKind> = {
  running: "running",
  queued: "running",
  awaiting_input: "warn",
  done: "ok",
  failed: "danger",
  cancelled: "stopped",
};

function short(sha?: string): string {
  return (sha || "").slice(0, 7);
}

function ago(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!t) return "";
  const s = Math.max(1, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}


export default function DeploysPanel({ slug }: { slug: string }) {
  const [deploys, setDeploys] = useState<Deploy[] | null>(null);
  const [providers, setProviders] = useState<ProviderCatalog[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [openRunsFor, setOpenRunsFor] = useState<string | null>(null);
  const [runsCache, setRunsCache] = useState<Record<string, Run[]>>({});
  const [runsLoading, setRunsLoading] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [confirmDeploy, setConfirmDeploy] = useState<Deploy | null>(null);
  const [confirmRollback, setConfirmRollback] = useState<{ deploy: Deploy; run: Run } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Deploy | null>(null);
  const [busyDeployId, setBusyDeployId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [dr, pr] = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(slug)}/deploys`),
        fetch(`/api/deploys/providers`),
      ]);
      if (!dr.ok) throw new Error(`deploys ${dr.status} ${await dr.text()}`);
      if (!pr.ok) throw new Error(`providers ${pr.status} ${await pr.text()}`);
      const dj = await dr.json();
      const pj = await pr.json();
      setDeploys(dj.deploys || []);
      setProviders(pj.providers || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [slug]);
  useEffect(() => { load(); }, [load]);

  const loadRuns = useCallback(async (deploy: Deploy) => {
    setRunsLoading(deploy.id);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/deploys/${encodeURIComponent(deploy.id)}/runs`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setRunsCache((c) => ({ ...c, [deploy.id]: j.runs || [] }));
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setRunsLoading(null);
    }
  }, [slug]);

  const toggleRuns = useCallback((deploy: Deploy) => {
    if (openRunsFor === deploy.id) {
      setOpenRunsFor(null);
      return;
    }
    setOpenRunsFor(deploy.id);
    if (!runsCache[deploy.id]) loadRuns(deploy);
  }, [openRunsFor, runsCache, loadRuns]);

  const kickDeploy = useCallback(async (deploy: Deploy) => {
    setBusyDeployId(deploy.id);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/deploys/${encodeURIComponent(deploy.id)}:deploy`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ref: null, message: null }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setBanner(`Deploy started for ${deploy.name}. Watch it in Runs.`);
      await load();
      if (openRunsFor === deploy.id) await loadRuns(deploy);
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyDeployId(null);
    }
  }, [slug, load, openRunsFor, loadRuns]);

  const kickRollback = useCallback(async (deploy: Deploy, run: Run) => {
    setBusyDeployId(deploy.id);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/deploys/${encodeURIComponent(deploy.id)}:rollback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ to_run_id: run.id }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setBanner(`Rollback started for ${deploy.name} to ${short(run.evidence?.commit_sha)}.`);
      await load();
      if (openRunsFor === deploy.id) await loadRuns(deploy);
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyDeployId(null);
    }
  }, [slug, load, openRunsFor, loadRuns]);

  const deleteDeploy = useCallback(async (deploy: Deploy) => {
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/deploys/${encodeURIComponent(deploy.id)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    }
  }, [slug, load]);

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Rocket aria-hidden />}>Deploys</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          <Button size="sm" variant="ghost" onClick={load}><RefreshCw aria-hidden /> Refresh</Button>
          <Button size="sm" variant="ghost" onClick={async () => {
            const r = await fetch(`/api/deploys/watcher/tick`, { method: "POST" });
            if (r.ok) { const j = await r.json(); setBanner(`Watcher tick: ${JSON.stringify(j.results.map((x: { name?: string; status: string }) => `${x.name}=${x.status}`).join(", "))}`); await load(); }
          }} disabled={loading}>Check for updates</Button>
          <Button size="sm" variant="primary" onClick={() => setAddOpen(true)} disabled={loading}>
            <Plus aria-hidden /> Add target
          </Button>
        </Row>
      </Row>

      {banner && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <Card>
            <Row style={{ alignItems: "center", gap: "var(--space-2)" }}>
              <TriangleAlert aria-hidden />
              <span style={{ flex: 1 }}>{banner}</span>
              <Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>
            </Row>
          </Card>
        </div>
      )}

      <Card>
        {loading ? (
          <Stack gap="sm"><Skeleton width="55%" /><Skeleton width="40%" /></Stack>
        ) : err ? (
          <StateBlock variant="error" title="Couldn't load deploys"
            hint={<code>GET /api/projects/{slug}/deploys ({err})</code>}
            action={<Button size="sm" onClick={load}>Retry</Button>} />
        ) : (deploys?.length ?? 0) === 0 ? (
          <StateBlock
            title="No deploy targets"
            hint="Add a target to publish this project to a hosted provider (Cloudflare Pages, or others as they land)."
            action={<Button size="sm" variant="primary" onClick={() => setAddOpen(true)}><Plus aria-hidden /> Add target</Button>} />
        ) : (
          <Stack gap="lg">
            {(deploys || []).map((d) => (
              <DeployRow
                key={d.id}
                deploy={d}
                busy={busyDeployId === d.id}
                onDeploy={() => (d.name === "prod" ? setConfirmDeploy(d) : kickDeploy(d))}
                onToggleRuns={() => toggleRuns(d)}
                onDelete={() => setConfirmDelete(d)}
                runsOpen={openRunsFor === d.id}
                runsLoading={runsLoading === d.id}
                runs={runsCache[d.id] || []}
                onRollback={(run) => setConfirmRollback({ deploy: d, run })}
              />
            ))}
          </Stack>
        )}
      </Card>

      {addOpen && (
        <AddTargetModal
          slug={slug}
          providers={providers || []}
          onClose={() => setAddOpen(false)}
          onCreated={async () => { setAddOpen(false); await load(); }}
        />
      )}

      {confirmDeploy && (
        <ConfirmDialog
          open={true}
          onCancel={() => setConfirmDeploy(null)}
          title={`Deploy ${confirmDeploy.name} to ${confirmDeploy.target.kind}?`}
          body={
            <>
              This will build and publish{" "}
              <strong>{confirmDeploy.source.branch || "HEAD"}</strong> to{" "}
              <strong>{confirmDeploy.domain || confirmDeploy.target.project_name || confirmDeploy.target.kind}</strong>.
              The previous version becomes rollback-eligible.
            </>
          }
          confirmLabel="Deploy"
          onConfirm={async () => {
            const d = confirmDeploy;
            setConfirmDeploy(null);
            await kickDeploy(d);
          }}
        />
      )}
      {confirmRollback && (
        <ConfirmDialog
          open={true}
          onCancel={() => setConfirmRollback(null)}
          title={`Rollback ${confirmRollback.deploy.name} to ${short(confirmRollback.run.evidence?.commit_sha) || confirmRollback.run.id}?`}
          body={
            <>
              This will promote a prior successful deploy back to production.
              The current version is preserved in run history.
            </>
          }
          confirmLabel="Rollback"
          onConfirm={async () => {
            const c = confirmRollback;
            setConfirmRollback(null);
            await kickRollback(c.deploy, c.run);
          }}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          open={true}
          onCancel={() => setConfirmDelete(null)}
          title={`Remove deploy target ${confirmDelete.name}?`}
          body={<>The live site is not affected; only the workshop record and its history filter are removed.</>}
          confirmLabel="Remove"
          onConfirm={async () => {
            const d = confirmDelete;
            setConfirmDelete(null);
            await deleteDeploy(d);
          }}
        />
      )}
    </section>
  );
}


// ---------- one row ---------------------------------------------------------


function DeployRow({
  deploy, busy, onDeploy, onToggleRuns, onDelete, runsOpen, runsLoading, runs, onRollback,
}: {
  deploy: Deploy;
  busy: boolean;
  onDeploy: () => void;
  onToggleRuns: () => void;
  onDelete: () => void;
  runsOpen: boolean;
  runsLoading: boolean;
  runs: Run[];
  onRollback: (r: Run) => void;
}) {
  const current = deploy.current;
  const branch = deploy.source.branch || "HEAD";
  const provider = deploy.target.kind;

  // Row-level status: derive from the most recent run if we have it, else from
  // whether a `current` pointer is set.
  const lastStatus: StatusKind = runs[0]?.status
    ? (JOB_KIND[runs[0].status] || "stopped")
    : current ? "ok" : "stopped";
  const lastLabel = runs[0]?.status || (current ? "live" : "never deployed");

  return (
    <div className="ws-card" style={{ padding: "var(--space-4)" }}>
      <Row wrap between style={{ alignItems: "flex-start", gap: "var(--space-3)" }}>
        <Stack gap="sm" style={{ flex: "1 1 20rem", minWidth: 0 }}>
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <span style={{ fontWeight: "var(--fw-semi)", fontSize: "var(--fs-md)" }}>{deploy.name}</span>
            <Badge>{provider}</Badge>
            <Tag hue="indigo">branch: {branch}</Tag>
            <StatusPill status={lastStatus}>{lastLabel}</StatusPill>
          </Row>
          {current?.url && (
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
              <a href={current.url} target="_blank" rel="noreferrer"
                 style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {current.url.replace(/^https?:\/\//, "")}
                <ExternalLink size={12} aria-hidden />
              </a>
              {current.commit_sha && (
                <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  v{short(current.commit_sha)}
                </span>
              )}
              {current.deployed_at && (
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {ago(current.deployed_at)}
                </span>
              )}
            </Row>
          )}
          {deploy.domain && !current?.url && (
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              domain: {deploy.domain} (not yet deployed)
            </span>
          )}
        </Stack>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          <Button size="sm" variant="primary" loading={busy} onClick={onDeploy}>
            <Rocket aria-hidden /> Deploy
          </Button>
          <Button size="sm" variant="ghost" onClick={onToggleRuns}
                  aria-expanded={runsOpen}>
            {runsOpen ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}
            History
          </Button>
          <Button size="sm" variant="danger-ghost" onClick={onDelete}>
            <Trash2 aria-hidden />
          </Button>
        </Row>
      </Row>
      {runsOpen && (
        <div style={{ marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)" }}>
          {runsLoading ? (
            <Skeleton width="40%" />
          ) : runs.length === 0 ? (
            <StateBlock title="No runs yet" hint="Kick a deploy above to record the first attempt." />
          ) : (
            <Stack gap="sm">
              {runs.map((r) => (
                <Row key={r.id} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <StatusDot status={JOB_KIND[r.status] || "stopped"}>{r.status}</StatusDot>
                  {r.kind === "deploy-rollback" && <Badge>rollback</Badge>}
                  <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {r.evidence?.commit_sha ? short(r.evidence.commit_sha) : short(r.binding?.ref)}
                  </span>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{ago(r.started_at)}</span>
                  {r.evidence?.summary && (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.evidence.summary}
                    </span>
                  )}
                  <Link to={`/work?tab=runs&job=${encodeURIComponent(r.id)}`} style={{ textDecoration: "none" }}>
                    <Button size="sm" variant="ghost">View run</Button>
                  </Link>
                  {r.status === "done" && r.kind === "deploy" && (
                    <Button size="sm" variant="ghost" onClick={() => onRollback(r)}>
                      <RotateCcw aria-hidden /> Rollback here
                    </Button>
                  )}
                </Row>
              ))}
            </Stack>
          )}
        </div>
      )}
    </div>
  );
}


// ---------- add target modal ----------------------------------------------


function AddTargetModal({
  slug, providers, onClose, onCreated,
}: {
  slug: string;
  providers: ProviderCatalog[];
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const [name, setName] = useState("prod");
  const [providerKind, setProviderKind] = useState(providers[0]?.kind || "");
  const [targetFields, setTargetFields] = useState<Record<string, string>>({});
  const [branch, setBranch] = useState("main");
  const [buildCmd, setBuildCmd] = useState("npm run build");
  const [outputDir, setOutputDir] = useState("dist");
  const [domain, setDomain] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const provider = useMemo(() => providers.find((p) => p.kind === providerKind), [providers, providerKind]);

  const submit = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      const target: Record<string, string | undefined> = { kind: providerKind };
      for (const f of provider?.target_fields || []) {
        if (targetFields[f.key]) target[f.key] = targetFields[f.key];
      }
      const body = {
        name,
        target,
        source: {
          kind: "git-branch",
          branch,
          build: { cmd: buildCmd, output_dir: outputDir },
        },
        domain: domain || null,
        autonomy: "reactive",
      };
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/deploys`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      await onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [name, providerKind, targetFields, branch, buildCmd, outputDir, domain, provider, slug, onCreated]);

  return (
    <Modal open onClose={onClose} title="Add deploy target">
      <Stack gap="lg">
        <Field label="Name" help='"prod" or "preview" or free-form'>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod" />
        </Field>
        <Field label="Provider">
          <Select value={providerKind} onChange={(e) => setProviderKind(e.target.value)}>
            {providers.map((p) => (
              <option key={p.kind} value={p.kind}>{p.label}</option>
            ))}
          </Select>
        </Field>
        {(provider?.target_fields || []).map((f) => (
          <Field key={f.key} label={f.key} help={f.description + (f.required ? " (required)" : "")}>
            <Input
              value={targetFields[f.key] || ""}
              onChange={(e) => setTargetFields((s) => ({ ...s, [f.key]: e.target.value }))}
              placeholder={f.key === "project_name" ? "cf-pages project name" : ""}
            />
          </Field>
        ))}
        <Field label="Branch">
          <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
        </Field>
        <Field label="Build command">
          <Input value={buildCmd} onChange={(e) => setBuildCmd(e.target.value)} placeholder="npm run build" />
        </Field>
        <Field label="Output directory" help="relative to project root, e.g. dist / build / out">
          <Input value={outputDir} onChange={(e) => setOutputDir(e.target.value)} placeholder="dist" />
        </Field>
        <Field label="Domain" help="display only; the provider owns the DNS record">
          <Input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" />
        </Field>
        {provider && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
            Requires env: {provider.env_requirements.filter((r) => r.required).map((r) => r.key).join(", ") || "none"}.
            Set them in the Environment panel below or in the workspace shared secrets.
          </p>
        )}
        {err && <StateBlock variant="error" title="Couldn't create target" hint={<code>{err}</code>} />}
        <Row style={{ gap: "var(--space-2)", justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="primary" onClick={submit} loading={saving} disabled={saving || !name || !providerKind}>
            Add target
          </Button>
        </Row>
      </Stack>
    </Modal>
  );
}
