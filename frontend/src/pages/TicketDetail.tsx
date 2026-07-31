import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Ticket as TicketIcon, ArrowLeft, RefreshCw, Trash2, Rocket, ExternalLink,
  Play, CheckCheck, XCircle, KeyRound, ScrollText, Ban,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Tag, Skeleton, Field, Input, Select, ConfirmDialog, Textarea, Markdown,
} from "../ui";
import {
  type Task, TASK_STATUSES, taskStatusKind, taskStatusLabel,
  title as titleOf, description as descriptionOf, joinText, scopeLabel, scopeHref,
  latitudeDesc, latitudeSourceLabel,
} from "../lib/tasks";


type JobRec = {
  id: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  trigger?: string;
  kind?: string;
  evidence?: { summary?: string; url?: string };
};

type Deploy = {
  id: string;
  project_slug: string;
  name: string;
  target: { kind: string; project_name?: string };
  source: { branch?: string };
  current?: { url?: string; commit_sha?: string; deployed_at?: string };
};


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


// A ticket's detail: title/description, status control, latitude, blocks graph
// (depends_on / relates_to), review verbs when the ticket is blocked, linked
// runs, and Deploy affordance when the scope maps to a project with configured
// deploy targets. The Runs tab of Work links here; the URL is stable so
// bookmarks and chat mentions survive.
export default function TicketDetail() {
  const { scope: scopeRaw = "", id = "" } = useParams();
  const scope = decodeURIComponent(scopeRaw);
  const nav = useNavigate();

  const [task, setTask] = useState<Task | null>(null);
  const [runs, setRuns] = useState<JobRec[] | null>(null);
  const [deploys, setDeploys] = useState<Deploy[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDesc, setDraftDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [deployPick, setDeployPick] = useState<string>("");
  const [deployBusy, setDeployBusy] = useState(false);

  const projectSlug = scope.startsWith("project:") ? scope.slice("project:".length) : null;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [tr, jr] = await Promise.all([
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`),
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/jobs`),
      ]);
      if (!tr.ok) throw new Error(`ticket ${tr.status} ${await tr.text()}`);
      if (!jr.ok) throw new Error(`runs ${jr.status} ${await jr.text()}`);
      setTask(await tr.json());
      const j = await jr.json();
      setRuns(j.jobs || []);
      if (projectSlug) {
        const dr = await fetch(`/api/projects/${encodeURIComponent(projectSlug)}/deploys`);
        if (dr.ok) {
          const dj = await dr.json();
          setDeploys(dj.deploys || []);
        } else {
          setDeploys([]);
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, id, projectSlug]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (task && !editing) {
      setDraftTitle(titleOf(task));
      setDraftDesc(descriptionOf(task));
    }
  }, [task, editing]);

  const patch = useCallback(async (fields: Record<string, unknown>) => {
    setSaving(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setTask(await r.json());
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [scope, id]);

  const saveText = useCallback(async () => {
    await patch({ text: joinText(draftTitle, draftDesc) });
    setEditing(false);
  }, [draftTitle, draftDesc, patch]);

  const runTask = useCallback(async () => {
    setRunning(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/run`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setBanner(`Run started: ${j.job_id || "(no id)"}`);
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      setRunOpen(false);
    }
  }, [scope, id, load]);

  const deleteTask = useCallback(async () => {
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      nav("/work?tab=tickets", { replace: true });
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    }
  }, [scope, id, nav]);

  const review = useCallback(async (action: string) => {
    setReviewBusy(action);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, note: reviewNote || null }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setReviewNote("");
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setReviewBusy(null);
    }
  }, [scope, id, reviewNote, load]);

  const kickDeploy = useCallback(async () => {
    if (!deployPick || !projectSlug) return;
    setDeployBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(projectSlug)}/deploys/${encodeURIComponent(deployPick)}:deploy`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ref: null, message: `From ticket ${id}` }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setBanner(`Deploy started: ${j.job_id}. Watch it in this ticket's Runs list.`);
      setDeployOpen(false);
      setDeployPick("");
      await load();
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    } finally {
      setDeployBusy(false);
    }
  }, [deployPick, projectSlug, id, load]);

  const blocked = task?.status === "blocked" && !!task?.awaiting;

  const dependsOn = task?.depends_on || [];
  const relatesTo = task?.relates_to || [];

  const scopeLink = scopeHref(scope);

  const workHref = "/work?tab=tickets" + (scope ? `&scope=${encodeURIComponent(scope)}` : "");

  return (
    <div className="ws-page">
      <PageHeader
        title={
          <Row style={{ alignItems: "center", gap: "var(--space-2)" }}>
            <Link to={workHref} style={{ display: "inline-flex", alignItems: "center" }} aria-label="Back to tickets">
              <ArrowLeft aria-hidden />
            </Link>
            <TicketIcon aria-hidden />
            <span>{loading ? "Ticket" : titleOf(task) || id}</span>
          </Row>
        }
        sub={
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            {task && (
              <>
                <StatusPill status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusPill>
                <Badge>{scopeLabel(scope)}</Badge>
                {scopeLink && (
                  <Link to={scopeLink} style={{ fontSize: "var(--fs-xs)" }}>
                    scope <ExternalLink size={10} aria-hidden />
                  </Link>
                )}
                {/* Back to the conversation that produced this ticket. The
                    reasoning lives there; a ticket without a route back to it
                    is a decision with its argument amputated. */}
                {task.origin?.context_key ? (
                  <Link
                    to={`/chat/${encodeURIComponent(task.origin.context_key)}?thread=${encodeURIComponent(task.origin.id)}`}
                    style={{ fontSize: "var(--fs-xs)" }}
                  >
                    from "{task.origin.title}" <ExternalLink size={10} aria-hidden />
                  </Link>
                ) : task.origin ? (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    from a chat that has since been deleted
                  </span>
                ) : null}
                <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{id}</span>
              </>
            )}
          </Row>
        }
        actions={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button size="sm" variant="ghost" onClick={load}><RefreshCw aria-hidden /> Refresh</Button>
            <Button size="sm" variant="primary" onClick={() => setRunOpen(true)} disabled={!task || saving || running}>
              <Play aria-hidden /> Run
            </Button>
            {projectSlug && (deploys?.length ?? 0) > 0 && (
              <Button size="sm" variant="ghost" onClick={() => setDeployOpen(true)}>
                <Rocket aria-hidden /> Deploy
              </Button>
            )}
            <Button size="sm" variant="danger-ghost" onClick={() => setConfirmDelete(true)}>
              <Trash2 aria-hidden />
            </Button>
          </Row>
        }
      />

      {banner && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <Card>
            <Row style={{ alignItems: "center", gap: "var(--space-2)" }}>
              <span style={{ flex: 1 }}>{banner}</span>
              <Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>
            </Row>
          </Card>
        </div>
      )}

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="40%" /></Stack></Card>
      ) : err || !task ? (
        <StateBlock variant="error" title="Couldn't load ticket"
          hint={<code>{err || "not found"}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : (
        <Stack gap="lg">
          {/* Body: title + description --------------------------------- */}
          <section>
            <Row between style={{ marginBottom: "var(--space-2)", alignItems: "center" }}>
              <SectionLabel>Ticket</SectionLabel>
              {!editing ? (
                <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
              ) : (
                <Row style={{ gap: "var(--space-2)" }}>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                  <Button size="sm" variant="primary" onClick={saveText} loading={saving} disabled={saving}>Save</Button>
                </Row>
              )}
            </Row>
            <Card>
              {editing ? (
                <Stack gap="sm">
                  <Field label="Title">
                    <Input value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} placeholder="Ticket title" />
                  </Field>
                  <Field label="Description">
                    <Textarea rows={6} value={draftDesc} onChange={(e) => setDraftDesc(e.target.value)}
                              placeholder="What needs to happen, and why." />
                  </Field>
                </Stack>
              ) : (
                <Stack gap="sm">
                  {descriptionOf(task) ? (
                    <Markdown>{descriptionOf(task)}</Markdown>
                  ) : (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                      No description. Click Edit to add one.
                    </span>
                  )}
                </Stack>
              )}
            </Card>
          </section>

          {/* Status + latitude + assignee ------------------------------ */}
          <section>
            <SectionLabel>State</SectionLabel>
            <Card>
              <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                <Field label="Status">
                  <Select value={task.status} onChange={(e) => patch({ status: e.target.value })} disabled={saving}>
                    {TASK_STATUSES.map((s) => (
                      <option key={s} value={s}>{taskStatusLabel(s)}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Latitude"
                  help={
                    <>
                      {task.effective_latitude?.value ? latitudeDesc(task.effective_latitude.value) : ""}
                      {task.effective_latitude?.source
                        ? ` — ${latitudeSourceLabel(task.effective_latitude.source)}`
                        : ""}
                    </>
                  }>
                  <Select value={task.latitude ?? "inherit"} onChange={(e) => patch({ latitude: e.target.value === "inherit" ? null : e.target.value })} disabled={saving}>
                    <option value="inherit">inherit</option>
                    <option value="strict">strict</option>
                    <option value="standard">standard</option>
                    <option value="open">open</option>
                  </Select>
                </Field>
                {task.assignee && (
                  <Field label="Assignee">
                    <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{task.assignee}</span>
                  </Field>
                )}
                {task.priority != null && (
                  <Field label="Priority">
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.priority}</span>
                  </Field>
                )}
              </Row>
            </Card>
          </section>

          {/* Review (block loop) --------------------------------------- */}
          {blocked && task.awaiting && (
            <section>
              <SectionLabel icon={<KeyRound aria-hidden />}>Awaiting an answer</SectionLabel>
              <Card>
                <Stack gap="sm">
                  <div>
                    <span style={{ fontWeight: "var(--fw-semi)" }}>
                      {task.awaiting.ask || task.awaiting.question || "This ticket blocked and needs a review."}
                    </span>
                    {task.awaiting.why && (
                      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--space-1) 0 0" }}>
                        {task.awaiting.why}
                      </p>
                    )}
                  </div>
                  {task.awaiting.proposal?.uri && (
                    <div style={{ fontSize: "var(--fs-xs)" }}>
                      Proposed grant: <code>{task.awaiting.proposal.verb}</code> on{" "}
                      <code>{task.awaiting.proposal.uri}</code>
                    </div>
                  )}
                  <Field label="Your note (recorded as evidence for the next attempt)">
                    <Textarea rows={3} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)}
                              placeholder="What did you decide, and why." />
                  </Field>
                  <Row wrap style={{ gap: "var(--space-2)" }}>
                    <Button size="sm" variant="primary" loading={reviewBusy === "approve"}
                            onClick={() => review("approve")}>
                      <CheckCheck aria-hidden /> Release
                    </Button>
                    {task.awaiting.proposal && (
                      <Button size="sm" variant="ghost" loading={reviewBusy === "grant-and-continue"}
                              onClick={() => review("grant-and-continue")}>
                        Grant & release
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" loading={reviewBusy === "mint-rule"}
                            onClick={() => review("mint-rule")}>
                      Mint rule & release
                    </Button>
                    <Button size="sm" variant="ghost" loading={reviewBusy === "dismiss"}
                            onClick={() => review("dismiss")}>
                      <Ban aria-hidden /> Dismiss
                    </Button>
                    <Button size="sm" variant="danger-ghost" loading={reviewBusy === "deny"}
                            onClick={() => review("deny")}>
                      <XCircle aria-hidden /> Deny (cancel ticket)
                    </Button>
                  </Row>
                </Stack>
              </Card>
            </section>
          )}

          {/* Depends on / relates to ---------------------------------- */}
          {(dependsOn.length > 0 || relatesTo.length > 0) && (
            <section>
              <SectionLabel>Links</SectionLabel>
              <Card>
                <Stack gap="sm">
                  {dependsOn.length > 0 && (
                    <div>
                      <span className="ws-card__meta">depends on</span>
                      <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-1)" }}>
                        {dependsOn.map((tid) => (
                          <Link key={tid} to={`/tickets/${encodeURIComponent(scope)}/${encodeURIComponent(tid)}`}
                                style={{ fontSize: "var(--fs-sm)" }}>
                            <Tag hue="orchid">{tid}</Tag>
                          </Link>
                        ))}
                      </Row>
                    </div>
                  )}
                  {relatesTo.length > 0 && (
                    <div>
                      <span className="ws-card__meta">relates to</span>
                      <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-1)" }}>
                        {relatesTo.map((tid) => (
                          <Link key={tid} to={`/tickets/${encodeURIComponent(scope)}/${encodeURIComponent(tid)}`}
                                style={{ fontSize: "var(--fs-sm)" }}>
                            <Tag hue="turquoise">{tid}</Tag>
                          </Link>
                        ))}
                      </Row>
                    </div>
                  )}
                </Stack>
              </Card>
            </section>
          )}

          {/* Runs ------------------------------------------------------ */}
          <section>
            <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
            <Card>
              {(runs?.length ?? 0) === 0 ? (
                <StateBlock title="No runs yet"
                  hint="Runs appear here every time this ticket is attempted (manually or by an agent sweep)." />
              ) : (
                <Stack gap="sm">
                  {(runs || []).map((r) => (
                    <Row key={r.id} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <StatusDot status={r.status === "done" ? "ok" : r.status === "failed" ? "danger" : r.status === "running" ? "running" : "stopped"}>
                        {r.status}
                      </StatusDot>
                      {r.kind && <Badge>{r.kind}</Badge>}
                      {r.trigger && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{r.trigger}</span>}
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{ago(r.started_at)}</span>
                      {r.evidence?.summary && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {r.evidence.summary}
                        </span>
                      )}
                      <Link to={`/runs/${encodeURIComponent(r.id)}`} style={{ textDecoration: "none" }}>
                        <Button size="sm" variant="ghost">Open</Button>
                      </Link>
                    </Row>
                  ))}
                </Stack>
              )}
            </Card>
          </section>

          {/* Deploys (project-scoped tickets only) --------------------- */}
          {projectSlug && (deploys?.length ?? 0) > 0 && (
            <section>
              <SectionLabel icon={<Rocket aria-hidden />}>Deploy from this ticket</SectionLabel>
              <Card>
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Field label="Target">
                    <Select value={deployPick} onChange={(e) => setDeployPick(e.target.value)}>
                      <option value="">Pick a target</option>
                      {(deploys || []).map((d) => (
                        <option key={d.id} value={d.id}>{d.name} · {d.target.kind}</option>
                      ))}
                    </Select>
                  </Field>
                  <Button size="sm" variant="primary" disabled={!deployPick || deployBusy}
                          loading={deployBusy}
                          onClick={() => setDeployOpen(true)}>
                    <Rocket aria-hidden /> Deploy
                  </Button>
                </Row>
                <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-2) 0 0" }}>
                  This kicks a deploy tied to this ticket (the resulting Run appears above and in the project's Deploys panel).
                </p>
              </Card>
            </section>
          )}
        </Stack>
      )}

      {runOpen && (
        <ConfirmDialog
          open
          onCancel={() => setRunOpen(false)}
          title="Run this ticket now?"
          body={<>A worker will pick this ticket up in the next sweep. Runs appear in the Runs section above.</>}
          confirmLabel="Run"
          onConfirm={runTask}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          open
          onCancel={() => setConfirmDelete(false)}
          title="Delete this ticket?"
          body={<>Ticket history is preserved in the Runs feed, but the ticket record is removed. This cannot be undone.</>}
          confirmLabel="Delete"
          onConfirm={deleteTask}
        />
      )}

      {deployOpen && deployPick && (
        <ConfirmDialog
          open
          onCancel={() => setDeployOpen(false)}
          title="Kick a deploy for this ticket?"
          body={
            <>
              Target: <strong>{deploys?.find((d) => d.id === deployPick)?.name}</strong> ({deploys?.find((d) => d.id === deployPick)?.target.kind}).
              The deploy Run appears above; the ticket status is unchanged.
            </>
          }
          confirmLabel="Deploy"
          onConfirm={kickDeploy}
        />
      )}
    </div>
  );
}
