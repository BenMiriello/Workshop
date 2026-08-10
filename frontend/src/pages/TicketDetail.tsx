import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Ticket as TicketIcon, ArrowLeft, RefreshCw, Trash2, Rocket, ExternalLink,
  Play, CheckCheck, XCircle, KeyRound, ScrollText, Ban, ClipboardList,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Tag, Skeleton, Field, Input, Select, ConfirmDialog, Modal, Textarea, Markdown,
  catFor,
} from "../ui";
import {
  type Task, TASK_STATUSES, taskStatusKind, taskStatusLabel,
  title as titleOf, description as descriptionOf, scopeLabel, scopeHref,
  cleanTitle, isBlockingCheck, readinessLabel,
  ticketHref,
  latitudeDesc, latitudeSourceLabel,
} from "../lib/tasks";
import { jobIsLive, jobStatusKind } from "../lib/jobs";
import { pickupFor, pickupNote, type PickupMap } from "../lib/scopePickup";
import { nextWorkParams } from "../lib/workParams";

// A ticket can only be launched from `todo`. This gate lived in Work.tsx's
// unreachable Edit drawer and nowhere on the live path, so Run was offered on
// `done` and `cancelled` tickets and simply re-ran finished work.
const RUNNABLE_STATUSES = new Set(["todo"]);

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
  const [pickupMap, setPickupMap] = useState<PickupMap | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDesc, setDraftDesc] = useState("");
  // Status and latitude are ticket CONFIG, so they commit on Save with the rest
  // of the ticket, not the instant the select moves (frontend/CLAUDE.md, "When
  // does a control commit?"). They used to PATCH on change.
  const [draftStatus, setDraftStatus] = useState("todo");
  const [draftLatitude, setDraftLatitude] = useState("inherit");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerErr, setBannerErr] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [deployPick, setDeployPick] = useState<string>("");
  const [deployBusy, setDeployBusy] = useState(false);

  const projectSlug = scope.startsWith("project:") ? scope.slice("project:".length) : null;

  // One banner, two meanings. A 409 used to land here as raw JSON.
  const say = useCallback((text: string) => { setBanner(text); setBannerErr(false); }, []);
  const fail = useCallback((text: string) => { setBanner(text); setBannerErr(true); }, []);

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

  // Whether anything picks this scope's tickets up on its own, and whether a
  // worker can be launched here at all.
  useEffect(() => {
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : { pickup: null }))
      .then((j) => setPickupMap(j.pickup ?? null))
      .catch(() => setPickupMap(null));
  }, []);

  useEffect(() => {
    if (task && !editing) {
      setDraftTitle(titleOf(task));
      setDraftDesc(descriptionOf(task));
      setDraftStatus(task.status ?? "todo");
      setDraftLatitude(task.latitude ?? "inherit");
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
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [scope, id, fail]);

  // Patches the canonical pair, not the joined blob: the store keeps title and
  // body as separate fields, and sending `text` would make the server re-split
  // an edit the operator already split by hand. Status and latitude ride along,
  // because they are part of the same explicit Save.
  const saveEdits = useCallback(async () => {
    await patch({
      title: cleanTitle(draftTitle),
      body: draftDesc,
      status: draftStatus,
      latitude: draftLatitude === "inherit" ? null : draftLatitude,
    });
    setEditing(false);
  }, [draftTitle, draftDesc, draftStatus, draftLatitude, patch]);

  // A draft is a suggestion the system made. Approving is the operator agreeing
  // to it, and it is the only thing that makes a ticket eligible to run — the
  // sweep skips drafts and the run endpoint refuses them.
  const approve = useCallback(async () => {
    setApproving(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ run: false }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      // Approving does not require a well-specified ticket, but it should not be
      // silent about approving one that is not: the checklist is the reason the
      // draft step exists.
      const gaps = j?.readiness?.missing?.length ?? 0;
      say(gaps
        ? `Approved and queued, with ${gaps} thing${gaps === 1 ? "" : "s"} the ticket still does not say.`
        : "Approved and queued.");
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setApproving(false);
    }
  }, [scope, id, load, say, fail]);

  const runTask = useCallback(async () => {
    setRunning(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/run`, { method: "POST" });
      // A busy tree answers 409. It used to render as the raw JSON body of the
      // error, which tells the operator nothing about what to do next.
      if (r.status === 409) {
        const e = await r.json().catch(() => ({} as { detail?: unknown }));
        const detail = typeof e.detail === "string" ? e.detail : "";
        fail(detail || "A sweep is already running against this scope. Wait for it to finish, or stop the queue runner.");
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      say(
        j.job_id
          ? "Run started. It appears under Runs below, and the ticket status moves when the run finishes."
          : "The run was accepted but returned no id.",
      );
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      setRunOpen(false);
    }
  }, [scope, id, load, say, fail]);

  const deleteTask = useCallback(async () => {
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      nav(`/work?${nextWorkParams(new URLSearchParams(), { tab: "tickets", scope })}`, { replace: true });
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    }
  }, [scope, id, nav, fail]);

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
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setReviewBusy(null);
    }
  }, [scope, id, reviewNote, load, fail]);

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
      say(`Deploy started: ${j.job_id}. Watch it in this ticket's Runs list.`);
      setDeployOpen(false);
      setDeployPick("");
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setDeployBusy(false);
    }
  }, [deployPick, projectSlug, id, load, say, fail]);

  const blocked = task?.status === "blocked" && !!task?.awaiting;

  const dependsOn = task?.depends_on || [];
  const relatesTo = task?.relates_to || [];

  const scopeLink = scopeHref(scope);

  const workHref = `/work?${nextWorkParams(new URLSearchParams(), { tab: "tickets", scope })}`;

  // A run this ticket started is a background process. The ticket record itself
  // does not move when `launch_task_run` opens a Job, so without this the status
  // pill still reads "To do" after a successful launch and the page reads as if
  // nothing happened. Say that a run is in flight, and poll until it is not.
  const liveRun = (runs || []).find((r) => jobIsLive(r.status)) || null;
  const liveRunId = liveRun?.id ?? null;
  useEffect(() => {
    if (!liveRunId) return;
    const t = setInterval(() => { void load(); }, 5000);
    return () => clearInterval(t);
  }, [liveRunId, load]);

  const pickup = pickupFor(pickupMap, scope);
  const pickupWarning = pickupNote(pickup);
  const statusRunnable = RUNNABLE_STATUSES.has(task?.status ?? "");
  const canRun = !!task && !saving && !running && !liveRunId && statusRunnable && pickup.runnable;
  const cannotRunBecause = !task
    ? ""
    : liveRunId ? "A run for this ticket is already in flight."
    : !pickup.runnable ? "This scope cannot run tickets."
    : !statusRunnable ? `Run is offered while a ticket is To do. This one is ${taskStatusLabel(task.status)}.`
    : "";

  const dirty = !!task && editing && (
    draftTitle !== titleOf(task)
    || draftDesc !== descriptionOf(task)
    || draftStatus !== (task.status ?? "todo")
    || draftLatitude !== (task.latitude ?? "inherit")
  );

  return (
    <div>
      <PageHeader
        title={
          <Row style={{ alignItems: "center", gap: "var(--space-2)" }}>
            <Link to={workHref} style={{ display: "inline-flex", alignItems: "center" }} aria-label="Back to tickets">
              <ArrowLeft aria-hidden />
            </Link>
            <TicketIcon aria-hidden />
            <span className="ws-wrap">{loading ? "Ticket" : titleOf(task) || id}</span>
          </Row>
        }
        sub={
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            {task && (
              <>
                <StatusPill status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusPill>
                {/* The ticket record does not move when a run is launched, so
                    the two facts are shown as two pills rather than one lie. */}
                {liveRunId && <StatusPill status="running">run in flight</StatusPill>}
                {/* A hued Tag, matching Work's ticket card. The same scope
                    rendering as a neutral Badge here and a coloured Tag there
                    made one fact look like two different kinds of fact. */}
                <Tag hue={catFor(scope || "workshop")}>{scopeLabel(scope)}</Tag>
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
            {task?.status === "draft" && (
              <Button size="sm" variant="primary" onClick={approve}
                      loading={approving} disabled={approving}>
                <CheckCheck aria-hidden /> Approve
              </Button>
            )}
            <Button
              size="sm"
              variant={task?.status === "draft" ? "ghost" : "primary"}
              onClick={() => setRunOpen(true)}
              disabled={!canRun}
              title={cannotRunBecause || undefined}
            >
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
          {bannerErr ? (
            <StateBlock
              variant="error"
              title="That didn't work"
              hint={banner}
              action={<Button size="sm" onClick={() => setBanner(null)}>Dismiss</Button>}
            />
          ) : (
            <Card>
              <Row style={{ alignItems: "center", gap: "var(--space-2)" }}>
                <span style={{ flex: 1 }}>{banner}</span>
                <Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>
              </Row>
            </Card>
          )}
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
                <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  {dirty && <Badge>unsaved</Badge>}
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                  <Button size="sm" variant="primary" onClick={saveEdits} loading={saving} disabled={saving || !dirty}>Save</Button>
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

          {/* What the ticket does not yet say -------------------------- */}
          {task.readiness && !task.readiness.ok && (
            <section>
              <SectionLabel icon={<ClipboardList aria-hidden />}>Not yet specified</SectionLabel>
              <Card>
                <Stack gap="sm">
                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                    A worker only ever sees the ticket, which does not answer these yet.
                  </span>
                  {task.readiness.missing.map((m) => (
                    <StatusDot key={m.check}
                               status={isBlockingCheck(m.check) ? "danger" : "warn"}>
                      <strong>{readinessLabel(m.check)}</strong>
                      <span className="ws-muted">: {m.why}</span>
                    </StatusDot>
                  ))}
                  {task.readiness.warnings.map((w) => (
                    <StatusDot key={w} status="warn">
                      <span className="ws-muted">{w}</span>
                    </StatusDot>
                  ))}
                </Stack>
              </Card>
            </section>
          )}

          {/* Status + latitude + assignee ------------------------------ */}
          <section>
            <SectionLabel>State</SectionLabel>
            <Card>
              <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                {/* Config, so it commits on the ticket's explicit Save, not the
                    instant the select moves. Read-only until Edit is pressed. */}
                <Field label="Status">
                  {editing ? (
                    <Select value={draftStatus} onChange={(e) => setDraftStatus(e.target.value)} disabled={saving}>
                      {TASK_STATUSES.map((s) => (
                        <option key={s} value={s}>{taskStatusLabel(s)}</option>
                      ))}
                    </Select>
                  ) : (
                    <StatusDot status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusDot>
                  )}
                </Field>
                <Field label="Latitude"
                  help={
                    <>
                      {task.effective_latitude?.value ? latitudeDesc(task.effective_latitude.value) : ""}
                      {task.effective_latitude?.source
                        ? ` (${latitudeSourceLabel(task.effective_latitude.source)})`
                        : ""}
                    </>
                  }>
                  {editing ? (
                    <Select value={draftLatitude} onChange={(e) => setDraftLatitude(e.target.value)} disabled={saving}>
                      <option value="inherit">inherit</option>
                      <option value="strict">strict</option>
                      <option value="standard">standard</option>
                      <option value="open">open</option>
                    </Select>
                  ) : (
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.latitude ?? "inherit"}</span>
                  )}
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
              {!editing && (
                <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-2) 0 0" }}>
                  Press Edit above to change the status or latitude.
                </p>
              )}
              {/* What will actually happen to this ticket if nobody presses
                  Run. For a project scope today, the answer is nothing. */}
              {pickupWarning && (
                <div style={{ marginTop: "var(--space-2)" }}>
                  <StatusDot status="warn">
                    <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{pickupWarning}</span>
                  </StatusDot>
                </div>
              )}
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
                  <Field label="Your note" help="The next attempt reads this.">
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
                          <Link key={tid} to={ticketHref(scope, tid)}
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
                          <Link key={tid} to={ticketHref(scope, tid)}
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
                      {/* jobStatusKind, not a local ternary. The inlined one
                          here had no awaiting_input case, so a run waiting on
                          the operator rendered grey on this page and amber on
                          every other one. */}
                      <StatusDot status={jobStatusKind(r.status)}>{r.status}</StatusDot>
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
                  <Button size="sm" variant="primary" disabled={deployBusy}
                          onClick={() => setDeployOpen(true)}>
                    <Rocket aria-hidden /> Choose a target
                  </Button>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {(deploys?.length ?? 0)} target{(deploys?.length ?? 0) === 1 ? "" : "s"} configured for {projectSlug}
                  </span>
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
          body={
            <>
              This launches a worker against this ticket immediately, in{" "}
              <strong>{scopeLabel(scope)}</strong>. The run appears in the Runs section above.
              The ticket keeps its current status until the run finishes.
            </>
          }
          confirmLabel="Run"
          busy={running}
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

      {/* The dialog CARRIES the target picker. It used to be gated on
          `deployOpen && deployPick`, with deployPick set only by a Select
          further down the page, so the header's Deploy button opened nothing at
          all until the operator had already chosen a target somewhere else. */}
      {deployOpen && (
        <Modal
          open
          onClose={() => setDeployOpen(false)}
          title="Kick a deploy for this ticket?"
          actions={
            <>
              <Button onClick={() => setDeployOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                disabled={!deployPick || deployBusy}
                loading={deployBusy}
                onClick={kickDeploy}
              >
                <Rocket aria-hidden /> Deploy
              </Button>
            </>
          }
        >
          <Stack gap="lg">
            <Field label="Target" htmlFor="ticket-deploy-target">
              <Select
                id="ticket-deploy-target"
                value={deployPick}
                onChange={(e) => setDeployPick(e.target.value)}
                data-autofocus
              >
                <option value="">Pick a target</option>
                {(deploys || []).map((d) => (
                  <option key={d.id} value={d.id}>{d.name} · {d.target.kind}</option>
                ))}
              </Select>
            </Field>
            <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
              The deploy Run appears in this ticket's Runs list and in the project's Deploys panel.
              The ticket status is unchanged.
            </p>
          </Stack>
        </Modal>
      )}
    </div>
  );
}
