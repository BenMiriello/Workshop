import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ChevronLeft, MoreHorizontal, RefreshCw, ExternalLink, Trash2, RotateCcw, ScrollText,
  TriangleAlert, Play,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, Notice, StatusDot,
  Badge, Skeleton, Field, Input, Select, ConfirmDialog, Modal, Menu, MenuItem, type StatusKind,
} from "@workshop/ui";
import { runHref } from "../lib/jobs";
import { shortHttpError } from "../lib/httpError";


type Deploy = {
  id: string;
  project_slug: string;
  name: string;
  target: { kind: string; project_name?: string; account_id?: string; host?: string; path?: string; releases_subdir?: string; current_symlink?: string };
  source: { kind: string; branch?: string; build?: { cmd?: string; output_dir?: string; timeout_sec?: number } };
  domain?: string;
  autonomy: string;
  created_at: string;
  updated_at: string;
  last_run_id?: string;
  current?: { run_id?: string; commit_sha?: string; deployed_at?: string; url?: string; upload_ref?: string };
};

type Run = {
  id: string;
  kind?: string;
  trigger?: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  binding?: { deploy_id?: string; ref?: string };
  evidence?: { summary?: string; url?: string; commit_sha?: string; upload_ref?: string; log_tail?: string; missing_keys?: string[] };
};

type Notice = { deploy_id?: string; branch?: string; remote_sha?: string; detected_at?: string; reason?: string; none?: boolean };


const JOB_KIND: Record<string, StatusKind> = {
  running: "running",
  queued: "running",
  awaiting_input: "warn",
  done: "ok",
  failed: "danger",
  cancelled: "stopped",
};

function short(sha?: string): string { return (sha || "").slice(0, 7); }

function ago(iso?: string | null): string {
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


export default function DeployDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();

  const [deploy, setDeploy] = useState<Deploy | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDeploy, setConfirmDeploy] = useState(false);
  const [confirmRollback, setConfirmRollback] = useState<Run | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<Deploy>>({});
  const [logOpen, setLogOpen] = useState<Run | null>(null);
  const [logText, setLogText] = useState<string>("");

  const load = useCallback(async () => {
    setErr(null);
    try {
      // We only know the deploy_id in the URL; look up which project it belongs
      // to via the cross-project list, then fetch the per-project routes.
      const listR = await fetch("/api/deploys");
      if (!listR.ok) throw new Error(`list: ${shortHttpError(listR.status, listR.statusText, await listR.text().catch(() => ""))}`);
      const listJ = await listR.json();
      const rec = (listJ.deploys as Deploy[]).find((d) => d.id === id);
      if (!rec) { setErr("deploy not found"); setLoading(false); return; }
      setDeploy(rec);
      const [runsR, noticeR] = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(rec.project_slug)}/deploys/${encodeURIComponent(id)}/runs`),
        fetch(`/api/projects/${encodeURIComponent(rec.project_slug)}/deploys/${encodeURIComponent(id)}/notice`),
      ]);
      if (runsR.ok) {
        const j = await runsR.json();
        setRuns(j.runs || []);
      }
      if (noticeR.ok) {
        setNotice(await noticeR.json());
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (deploy && !editing) {
      setDraft({
        name: deploy.name,
        target: { ...deploy.target },
        source: { ...deploy.source, build: { ...(deploy.source.build || {}) } },
        domain: deploy.domain || "",
        autonomy: deploy.autonomy,
      });
    }
  }, [deploy, editing]);

  const save = useCallback(async () => {
    if (!deploy) return;
    setBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(deploy.project_slug)}/deploys/${encodeURIComponent(deploy.id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setDeploy(await r.json());
      setEditing(false);
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [deploy, draft]);

  const kickDeploy = useCallback(async () => {
    if (!deploy) return;
    setBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(deploy.project_slug)}/deploys/${encodeURIComponent(deploy.id)}:deploy`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ref: null }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      setBanner(`Deploy started: ${j.job_id}.`);
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirmDeploy(false);
    }
  }, [deploy, load]);

  const kickRollback = useCallback(async (run: Run) => {
    if (!deploy) return;
    setBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(deploy.project_slug)}/deploys/${encodeURIComponent(deploy.id)}:rollback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ to_run_id: run.id }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      setBanner(`Rollback started: ${j.job_id}.`);
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirmRollback(null);
    }
  }, [deploy, load]);

  const del = useCallback(async () => {
    if (!deploy) return;
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(deploy.project_slug)}/deploys/${encodeURIComponent(deploy.id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      nav("/deploys", { replace: true });
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    }
  }, [deploy, nav]);

  const openLog = useCallback(async (run: Run) => {
    if (!deploy) return;
    setLogOpen(run);
    setLogText("Loading...");
    try {
      const r = await fetch(`/api/deploys/${encodeURIComponent(deploy.id)}/log/${encodeURIComponent(run.id)}?tail=500`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      setLogText(j.log || "(empty)");
    } catch (e) {
      setLogText(e instanceof Error ? e.message : String(e));
    }
  }, [deploy]);

  const lastStatus: StatusKind = runs[0]?.status
    ? (JOB_KIND[runs[0].status] || "stopped")
    : deploy?.current ? "ok" : "stopped";

  return (
    <div>
      <PageHeader
        variant="entity"
        back={<Link to="/deploys"><ChevronLeft aria-hidden /> Deploys</Link>}
        title={deploy ? deploy.name : "Deploy"}
        meta={
          deploy && (
            <>
              <StatusDot status={lastStatus}>
                {runs[0]?.status || (deploy.current ? "live" : "never deployed")}
              </StatusDot>
              <Link to={`/projects/${encodeURIComponent(deploy.project_slug)}`}>{deploy.project_slug}</Link>
              <span>{deploy.target.kind}</span>
              <span>branch {deploy.source.branch || "HEAD"}</span>
              <span>autonomy {deploy.autonomy}</span>
              {deploy.current?.url && (
                <a href={deploy.current.url} target="_blank" rel="noreferrer">
                  {deploy.current.url.replace(/^https?:\/\//, "")} <ExternalLink size={12} aria-hidden />
                </a>
              )}
            </>
          )
        }
        actions={
          // One primary action, the rest behind the overflow. The project link
          // left this row entirely: it is a fact about the deploy, so it is in
          // the meta line above, not a button competing with Deploy.
          <Row wrap>
            <Button size="sm" variant="primary" onClick={() => setConfirmDeploy(true)} disabled={!deploy || busy}>
              <Play aria-hidden /> Deploy
            </Button>
            <Menu
              label="More actions"
              trigger={
                <Button size="sm" variant="ghost" aria-label="More actions">
                  <MoreHorizontal aria-hidden />
                </Button>
              }
            >
              <MenuItem icon={<RefreshCw aria-hidden />} onSelect={load}>Refresh</MenuItem>
              <MenuItem
                danger
                icon={<Trash2 aria-hidden />}
                disabled={!deploy}
                onSelect={() => setConfirmDelete(true)}
              >
                Delete deploy
              </MenuItem>
            </Menu>
          </Row>
        }
      />

      {banner && (
        <Notice action={<Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>}>
          {banner}
        </Notice>
      )}

      {notice && !notice.none && notice.remote_sha && (
        <Notice tone="warn" icon={<TriangleAlert aria-hidden />}>
          Change detected at <code>{short(notice.remote_sha)}</code> on{" "}
          <code>{notice.branch}</code>, paused ({notice.reason}).
          {notice.detected_at ? ` Noticed ${ago(notice.detected_at)}.` : ""}
        </Notice>
      )}

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack></Card>
      ) : err || !deploy ? (
        <StateBlock variant="error" title="Couldn't load deploy"
          hint={<code>{err || "not found"}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : (
        <Stack gap="lg">
          {/* Config ---------------------------------------------------- */}
          <section>
            <Row between style={{ marginBottom: "var(--space-2)", alignItems: "center" }}>
              <SectionLabel>Configuration</SectionLabel>
              {!editing ? (
                <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
              ) : (
                <Row style={{ gap: "var(--space-2)" }}>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
                  <Button size="sm" variant="primary" onClick={save} loading={busy} disabled={busy}>Save</Button>
                </Row>
              )}
            </Row>
            <Card>
              {editing ? (
                <Stack gap="sm">
                  <Field label="Name">
                    <Input value={draft.name || ""} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
                  </Field>
                  <Field label="Autonomy" help="reactive = you deploy by hand. supervised = queued for you to release. autonomous = deploys on every push.">
                    <Select value={draft.autonomy || "reactive"}
                            onChange={(e) => setDraft((d) => ({ ...d, autonomy: e.target.value }))}>
                      <option value="reactive">reactive</option>
                      <option value="supervised">supervised</option>
                      <option value="autonomous">autonomous</option>
                    </Select>
                  </Field>
                  <Field label="Domain (display only)">
                    <Input value={draft.domain || ""} onChange={(e) => setDraft((d) => ({ ...d, domain: e.target.value }))} />
                  </Field>
                  <SectionLabel>Target</SectionLabel>
                  {Object.keys(draft.target || {}).filter((k) => k !== "kind").map((k) => (
                    <Field key={k} label={k}>
                      <Input value={String((draft.target as Record<string, string>)?.[k] ?? "")}
                             onChange={(e) => setDraft((d) => ({
                               ...d,
                               target: { ...(d.target || { kind: deploy.target.kind }), [k]: e.target.value },
                             }))} />
                    </Field>
                  ))}
                  <SectionLabel>Source</SectionLabel>
                  <Field label="Branch">
                    <Input value={draft.source?.branch || ""}
                           onChange={(e) => setDraft((d) => ({
                             ...d,
                             source: { ...(d.source || { kind: "git-branch" }), branch: e.target.value },
                           }))} />
                  </Field>
                  <Field label="Build command">
                    <Input value={draft.source?.build?.cmd || ""}
                           onChange={(e) => setDraft((d) => ({
                             ...d,
                             source: {
                               ...(d.source || { kind: "git-branch" }),
                               build: { ...(d.source?.build || {}), cmd: e.target.value },
                             },
                           }))} />
                  </Field>
                  <Field label="Output directory">
                    <Input value={draft.source?.build?.output_dir || ""}
                           onChange={(e) => setDraft((d) => ({
                             ...d,
                             source: {
                               ...(d.source || { kind: "git-branch" }),
                               build: { ...(d.source?.build || {}), output_dir: e.target.value },
                             },
                           }))} />
                  </Field>
                </Stack>
              ) : (
                <Stack gap="sm">
                  <Row wrap style={{ gap: "var(--space-3)" }}>
                    <Field label="Autonomy"><span>{deploy.autonomy}</span></Field>
                    {deploy.domain && <Field label="Domain"><span>{deploy.domain}</span></Field>}
                  </Row>
                  <SectionLabel>Target ({deploy.target.kind})</SectionLabel>
                  <Row wrap style={{ gap: "var(--space-3)" }}>
                    {Object.entries(deploy.target).filter(([k]) => k !== "kind").map(([k, v]) => (
                      <Field key={k} label={k}><span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{String(v)}</span></Field>
                    ))}
                  </Row>
                  <SectionLabel>Source ({deploy.source.kind})</SectionLabel>
                  <Row wrap style={{ gap: "var(--space-3)" }}>
                    <Field label="Branch"><span className="ws-mono">{deploy.source.branch}</span></Field>
                    <Field label="Build command"><span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{deploy.source.build?.cmd}</span></Field>
                    <Field label="Output"><span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{deploy.source.build?.output_dir}</span></Field>
                  </Row>
                </Stack>
              )}
            </Card>
          </section>

          {/* Runs ------------------------------------------------------ */}
          <section>
            <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
            <Card>
              {runs.length === 0 ? (
                <StateBlock title="No runs yet" hint="Kick a deploy above to record the first attempt." />
              ) : (
                <Stack gap="sm">
                  {runs.map((r) => (
                    <Row key={r.id} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <StatusDot status={JOB_KIND[r.status] || "stopped"}>{r.status}</StatusDot>
                      {r.kind === "deploy-rollback" && <Badge>rollback</Badge>}
                      {r.trigger === "event" && <Badge>auto</Badge>}
                      <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                        {r.evidence?.commit_sha ? short(r.evidence.commit_sha) : short(r.binding?.ref)}
                      </span>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{ago(r.started_at)}</span>
                      {r.evidence?.summary && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {r.evidence.summary}
                        </span>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => openLog(r)}>Log</Button>
                      <Link to={runHref(r.id)} style={{ textDecoration: "none" }}>
                        <Button size="sm" variant="ghost">Open</Button>
                      </Link>
                      {r.status === "done" && r.kind === "deploy" && (
                        <Button size="sm" variant="ghost" onClick={() => setConfirmRollback(r)}>
                          <RotateCcw aria-hidden /> Rollback
                        </Button>
                      )}
                    </Row>
                  ))}
                </Stack>
              )}
            </Card>
          </section>
        </Stack>
      )}

      {confirmDeploy && (
        <ConfirmDialog
          open
          onCancel={() => setConfirmDeploy(false)}
          title={`Deploy ${deploy?.name} now?`}
          body={<>Builds and publishes <strong>{deploy?.source.branch || "HEAD"}</strong> to <strong>{deploy?.target.kind}</strong>.</>}
          confirmLabel="Deploy"
          onConfirm={kickDeploy}
        />
      )}

      {confirmRollback && (
        <ConfirmDialog
          open
          onCancel={() => setConfirmRollback(null)}
          title={`Rollback to ${short(confirmRollback.evidence?.commit_sha) || confirmRollback.id}?`}
          body={<>The current version is preserved in run history.</>}
          confirmLabel="Rollback"
          onConfirm={() => kickRollback(confirmRollback)}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          open
          onCancel={() => setConfirmDelete(false)}
          title="Delete this deploy target?"
          body={<>The live site is not affected; only the workshop record and its history filter are removed.</>}
          confirmLabel="Delete"
          onConfirm={del}
        />
      )}

      {logOpen && (
        <Modal open onClose={() => setLogOpen(null)} title={`Log · ${logOpen.id}`}>
          <pre className="ws-mono" style={{
            fontSize: "var(--fs-xs)",
            whiteSpace: "pre-wrap",
            maxHeight: "60vh",
            overflowY: "auto",
            padding: "var(--space-3)",
            background: "var(--surface-muted)",
            borderRadius: "var(--radius-sm)",
          }}>
            {logText}
          </pre>
        </Modal>
      )}
    </div>
  );
}
