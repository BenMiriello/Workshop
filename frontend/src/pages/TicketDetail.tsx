import { useCallback, useEffect, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Ticket as TicketIcon, ArrowLeft, RefreshCw, Trash2, Rocket, ExternalLink,
  Play, CheckCheck, XCircle, KeyRound, ScrollText, Ban, ClipboardList, Scissors,
  MessageSquare, History, GitBranch, Link2, Send,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Tag, Skeleton, Field, Input, Select, ConfirmDialog, Modal, Textarea,
  catFor,
} from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";
import {
  type Task, type TaskLink, TASK_STATUSES, taskStatusKind, taskStatusLabel,
  title as titleOf, description as descriptionOf, scopeLabel, scopeHref,
  cleanTitle, isBlockingCheck, readinessLabel,
  ticketHref, taskAcceptanceLabel,
  latitudeDesc, latitudeSourceLabel,
} from "../lib/tasks";
import { jobIsLive, jobStatusKind } from "../lib/jobs";
import { pickupFor, pickupNote, type PickupMap } from "../lib/scopePickup";
import { nextWorkParams } from "../lib/workParams";
import { GrantDialog, RuleDialog } from "../components/ReviewDialogs";

// A ticket can only be launched from `todo`. This gate lived in Work.tsx's
// unreachable Edit drawer and nowhere on the live path, so Run was offered on
// `done` and `cancelled` tickets and simply re-ran finished work.
const RUNNABLE_STATUSES = new Set(["todo"]);

// The scope review's answer. Proposals only: applying a split is a second,
// explicit act, and the review never edits the ticket's own text.
type ScopeReview = {
  verdict: "ok" | "split" | "underspecified";
  reason: string;
  subtickets: { title: string; body: string }[];
  blockers: string[];
  stages: string[];
};

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
  name: string;
  target: { kind: string };
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

function fmtTs(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Same rule JobsFeed uses. A run row without a duration or an end time made this
// page's run list strictly worse than the feed it links to.
function runDuration(r: JobRec): string {
  if (!r.started_at) return "";
  const start = new Date(r.started_at).getTime();
  const end = r.ended_at ? new Date(r.ended_at).getTime() : NaN;
  if (isNaN(start) || isNaN(end)) return "";
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

// One linked ticket, as a row that actually goes somewhere. The href is built
// from the LINK's own scope, not the current ticket's: the two are often
// different and the page used to send every cross-scope dependency to a 404.
function LinkRow({ link }: { link: TaskLink }) {
  return (
    <Link to={ticketHref(link.scope, link.id)} className="ws-ticket-link">
      <StatusDot status={taskStatusKind(link.status)}>{taskStatusLabel(link.status)}</StatusDot>
      <span className="ws-truncate ws-ticket-link__title" title={link.title}>
        {link.title || link.id}
      </span>
      <Tag hue={catFor(link.scope)}>{scopeLabel(link.scope)}</Tag>
    </Link>
  );
}

// A ticket's detail. Everything the record already knew and never showed: the
// family it belongs to, the answers already given to it, what has been attempted
// and what the gate is. The URL is stable, so bookmarks and chat mentions
// survive.
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
  // The scope review: a judgement pass over whether this is ONE piece of work.
  // Only offered on a draft, because after approval a worker is about to spend
  // money on it and the answer stops being worth much.
  const [scopeCheck, setScopeCheck] = useState<ScopeReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerErr, setBannerErr] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewDialog, setReviewDialog] = useState<"grant" | "rule" | null>(null);
  const [confirmDeny, setConfirmDeny] = useState(false);
  const [deployOpen, setDeployOpen] = useState(false);
  const [deployPick, setDeployPick] = useState<string>("");
  const [deployBusy, setDeployBusy] = useState(false);
  const [commentDraft, setCommentDraft] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);

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
  // to it, and it is the only thing that makes a ticket eligible to run: the
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

  const scopeReview = useCallback(async () => {
    setReviewing(true);
    setScopeCheck(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/scope-review`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setScopeCheck(await r.json());
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setReviewing(false);
    }
  }, [scope, id, fail]);

  const applySplit = useCallback(async () => {
    if (!scopeCheck?.subtickets?.length) return;
    setSplitting(true);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/split`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ subtickets: scopeCheck.subtickets }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      say(`Split into ${j.subtickets.length} sub-tickets. Each needs approving.`);
      setScopeCheck(null);
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setSplitting(false);
    }
  }, [scope, id, scopeCheck, load, say, fail]);

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

  // `extra` carries the fields an action requires: `rule` for mint-rule,
  // `store_uri`/`verbs` for grant-and-continue. Both used to be sent empty from
  // this page, so mint-rule hard-400'd every time it was pressed.
  const review = useCallback(async (action: string, extra: Record<string, unknown> = {}) => {
    setReviewBusy(action);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, note: reviewNote || null, ...extra }),
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

  const postComment = useCallback(async () => {
    const text = commentDraft.trim();
    if (!text) return;
    setCommentBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/comments`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setTask(await r.json());
      setCommentDraft("");
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setCommentBusy(false);
    }
  }, [scope, id, commentDraft, fail]);

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

  // A block the operator already dismissed from the Review tab is answered. It
  // used to keep showing the whole review panel here, because this page checked
  // `awaiting` and the Review tab checked `awaiting && !dismissed`.
  const awaiting = task?.awaiting ?? null;
  const blocked = task?.status === "blocked" && !!awaiting && !awaiting.dismissed;

  const links = task?.links;
  const children = links?.children ?? [];
  const dependsOn = links?.depends_on ?? [];
  const relatesTo = links?.relates_to ?? [];
  const missingLinks = links?.missing ?? [];
  const hasLinks = dependsOn.length > 0 || relatesTo.length > 0 || missingLinks.length > 0;
  const comments = task?.comments ?? [];
  const evidence = task?.evidence ?? [];

  const scopeLink = scopeHref(scope);
  const workHref = `/work?${nextWorkParams(new URLSearchParams(), { tab: "tickets", scope })}`;

  // A run this ticket started is a background process. The ticket record itself
  // does not move when `launch_task_run` opens a Job, so without this the status
  // pill still reads "To do" after a successful launch and the page reads as if
  // nothing happened. Say that a run is in flight, and poll until it is not.
  const liveRun = (runs || []).find((r) => jobIsLive(r.status)) || null;
  const liveRunId = liveRun?.id ?? null;
  usePoll(load, 5000, { enabled: !!liveRunId, immediate: false });

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

  // Sub-ticket roll-up, derived and never stored. A parent is finished when its
  // children are; showing the count is the whole point of splitting.
  const childrenDone = children.filter(
    (c) => c.status === "done" || c.status === "cancelled").length;

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
                {/* ONE status pill on this page. The State card below used to
                    render the same fact again as a StatusDot two screens down. */}
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
                {/* When it was filed and when it last moved. Both were on the
                    record and neither was ever on the page. */}
                {task.created && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}
                        title={fmtTs(task.created)}>filed {ago(task.created)}</span>
                )}
                {task.updated && task.updated !== task.created && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}
                        title={fmtTs(task.updated)}>updated {ago(task.updated)}</span>
                )}
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
            <Button size="sm" variant="danger-ghost" onClick={() => setConfirmDelete(true)}
                    aria-label="Delete ticket">
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
                    <Textarea rows={10} value={draftDesc} onChange={(e) => setDraftDesc(e.target.value)}
                              placeholder="What needs to happen, and why." />
                  </Field>
                  {/* Status and latitude live in the same Save as the text, so
                      they are edited here rather than in a second card that
                      commits on a different gesture. */}
                  <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
                    <Field label="Status">
                      <Select value={draftStatus} onChange={(e) => setDraftStatus(e.target.value)} disabled={saving}>
                        {TASK_STATUSES.map((s) => (
                          <option key={s} value={s}>{taskStatusLabel(s)}</option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Latitude" help={latitudeDesc(draftLatitude) || "Inherited from the scope."}>
                      <Select value={draftLatitude} onChange={(e) => setDraftLatitude(e.target.value)} disabled={saving}>
                        <option value="inherit">inherit</option>
                        <option value="strict">strict</option>
                        <option value="standard">standard</option>
                        <option value="open">open</option>
                      </Select>
                    </Field>
                  </Row>
                </Stack>
              ) : (
                <Stack gap="sm">
                  {descriptionOf(task) ? (
                    <Markdown>{descriptionOf(task)}</Markdown>
                  ) : (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                      No description. Press Edit to add one.
                    </span>
                  )}
                </Stack>
              )}
            </Card>
          </section>

          {/* Scope review: is this one piece of work? ------------------- */}
          {task.status === "draft" && (
            <section>
              <SectionLabel icon={<Scissors aria-hidden />}>Scope</SectionLabel>
              <Card>
                <Stack gap="sm">
                  {!scopeCheck && (
                    <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                        Check whether this is one piece of work before approving it.
                      </span>
                      <Button size="sm" variant="ghost" onClick={scopeReview}
                              loading={reviewing} disabled={reviewing}>
                        Review scope
                      </Button>
                    </Row>
                  )}
                  {scopeCheck && (
                    <>
                      <StatusDot status={scopeCheck.verdict === "ok" ? "ok" : "warn"}>
                        <strong>{scopeCheck.verdict === "ok" ? "One piece of work"
                          : scopeCheck.verdict === "split" ? "Should be split" : "Underspecified"}</strong>
                        {scopeCheck.reason ? <span className="ws-muted">: {scopeCheck.reason}</span> : null}
                      </StatusDot>
                      {scopeCheck.blockers.length > 0 && (
                        <Stack gap="sm">
                          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Blockers</span>
                          {scopeCheck.blockers.map((b) => (
                            <StatusDot key={b} status="warn"><span className="ws-muted">{b}</span></StatusDot>
                          ))}
                        </Stack>
                      )}
                      {scopeCheck.stages.length > 0 && (
                        <Stack gap="sm">
                          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Stages, in order</span>
                          {scopeCheck.stages.map((st, i) => (
                            <span key={st} style={{ fontSize: "var(--fs-sm)" }}>{i + 1}. {st}</span>
                          ))}
                        </Stack>
                      )}
                      {scopeCheck.subtickets.length > 0 && (
                        <Stack gap="sm">
                          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                            Proposed sub-tickets
                          </span>
                          {scopeCheck.subtickets.map((st) => (
                            <span key={st.title} style={{ fontSize: "var(--fs-sm)" }}>{st.title}</span>
                          ))}
                        </Stack>
                      )}
                      <Row wrap style={{ gap: "var(--space-2)" }}>
                        {scopeCheck.subtickets.length > 1 && (
                          <Button size="sm" variant="primary" onClick={applySplit}
                                  loading={splitting} disabled={splitting}>
                            <Scissors aria-hidden /> Split into {scopeCheck.subtickets.length}
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => setScopeCheck(null)}>Dismiss</Button>
                      </Row>
                    </>
                  )}
                </Stack>
              </Card>
            </section>
          )}

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

          {/* Family: parent + sub-tickets ------------------------------ */}
          {/* `parent` has been written by the split endpoint since it shipped
              and rendered ZERO times, so using this page's own Split button
              produced a parent with no children and children with no way home. */}
          {(links?.parent || children.length > 0) && (
            <section>
              <SectionLabel icon={<GitBranch aria-hidden />}>Sub-tickets</SectionLabel>
              <Card>
                <Stack gap="sm">
                  {links?.parent && (
                    <div>
                      <span className="ws-card__meta">part of</span>
                      <div style={{ marginTop: "var(--space-1)" }}>
                        <LinkRow link={links.parent} />
                      </div>
                    </div>
                  )}
                  {children.length > 0 && (
                    <div>
                      <span className="ws-card__meta">
                        {childrenDone} of {children.length} done
                      </span>
                      <Stack gap="sm" style={{ marginTop: "var(--space-1)" }}>
                        {children.map((c) => <LinkRow key={`${c.scope}:${c.id}`} link={c} />)}
                      </Stack>
                      {childrenDone < children.length && (
                        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-2) 0 0" }}>
                          Only leaf tickets are run. This one finishes when its sub-tickets do.
                        </p>
                      )}
                    </div>
                  )}
                </Stack>
              </Card>
            </section>
          )}

          {/* Depends on / relates to ---------------------------------- */}
          {hasLinks && (
            <section>
              <SectionLabel icon={<Link2 aria-hidden />}>Links</SectionLabel>
              <Card>
                <Stack gap="sm">
                  {dependsOn.length > 0 && (
                    <div>
                      <span className="ws-card__meta">blocked until these are done</span>
                      <Stack gap="sm" style={{ marginTop: "var(--space-1)" }}>
                        {dependsOn.map((l) => <LinkRow key={`${l.scope}:${l.id}`} link={l} />)}
                      </Stack>
                    </div>
                  )}
                  {relatesTo.length > 0 && (
                    <div>
                      <span className="ws-card__meta">related</span>
                      <Stack gap="sm" style={{ marginTop: "var(--space-1)" }}>
                        {relatesTo.map((l) => <LinkRow key={`${l.scope}:${l.id}`} link={l} />)}
                      </Stack>
                    </div>
                  )}
                  {/* An id that resolves in no scope. Shown as broken rather
                      than linked, because a link to nothing is worse than a
                      label that admits the reference is dead. */}
                  {missingLinks.length > 0 && (
                    <div>
                      <span className="ws-card__meta">references that no longer resolve</span>
                      <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-1)" }}>
                        {missingLinks.map((tid) => (
                          <StatusDot key={tid} status="warn">
                            <span className="ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{tid}</span>
                          </StatusDot>
                        ))}
                      </Row>
                    </div>
                  )}
                </Stack>
              </Card>
            </section>
          )}

          {/* State ---------------------------------------------------- */}
          {/* Status is NOT repeated here. It is in the header, and the edit
              control for it is in the Ticket card's edit mode, so this card is
              only the facts that had no home at all. */}
          <section>
            <SectionLabel>State</SectionLabel>
            <Card>
              <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-start" }}>
                <Field label="Latitude"
                  help={
                    <>
                      {task.effective_latitude?.value ? latitudeDesc(task.effective_latitude.value) : ""}
                      {task.effective_latitude?.source
                        ? ` (${latitudeSourceLabel(task.effective_latitude.source)})`
                        : ""}
                    </>
                  }>
                  <span style={{ fontSize: "var(--fs-sm)" }}>
                    {task.effective_latitude?.value ?? "standard"}
                  </span>
                </Field>
                {/* Every ticket defaults to priority 0, so rendering it always
                    put the word "Priority 0" on every ticket in the system.
                    Work's card already hid it; the two now agree. */}
                {typeof task.priority === "number" && task.priority !== 0 && (
                  <Field label="Priority">
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.priority}</span>
                  </Field>
                )}
                {task.effort && (
                  <Field label="Effort">
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.effort}</span>
                  </Field>
                )}
                {/* The completion gate. This is what decides whether the work is
                    accepted, and it was rendered nowhere. */}
                {task.acceptance?.kind && (
                  <Field label="Done when" help={task.acceptance.spec || undefined}>
                    <span style={{ fontSize: "var(--fs-sm)" }}>
                      {taskAcceptanceLabel(task.acceptance.kind)}
                    </span>
                  </Field>
                )}
                {typeof task.attempts === "number" && task.attempts > 0 && (
                  <Field label="Attempts"
                         help={task.max_attempts ? `Cap ${task.max_attempts}.` : undefined}>
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.attempts}</span>
                  </Field>
                )}
                {task.assignee && task.assignee !== "operator" && (
                  <Field label="Assignee">
                    <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{task.assignee}</span>
                  </Field>
                )}
                {task.scoped_by && (
                  <Field label="Scoped by" help="Who wrote this spec.">
                    <span style={{ fontSize: "var(--fs-sm)" }}>{task.scoped_by}</span>
                  </Field>
                )}
              </Row>
              {(task.labels?.length ?? 0) > 0 && (
                <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
                  {(task.labels ?? []).map((l) => <Tag key={l} hue={catFor(l)}>{l}</Tag>)}
                </Row>
              )}
              {task.blocked_reason && (
                <div style={{ marginTop: "var(--space-2)" }}>
                  <StatusDot status="danger">
                    <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{task.blocked_reason}</span>
                  </StatusDot>
                </div>
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
          {blocked && awaiting && (
            <section>
              <SectionLabel icon={<KeyRound aria-hidden />}>Awaiting an answer</SectionLabel>
              <Card>
                <Stack gap="sm">
                  <div>
                    <span style={{ fontWeight: "var(--fw-semi)" }}>
                      {awaiting.ask || awaiting.question || "This ticket blocked and needs a review."}
                    </span>
                    {awaiting.why && (
                      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--space-1) 0 0" }}>
                        {awaiting.why}
                      </p>
                    )}
                  </div>
                  {awaiting.proposal?.uri && (
                    <div style={{ fontSize: "var(--fs-xs)" }}>
                      Proposed grant: <code>{awaiting.proposal.verb}</code> on{" "}
                      <code>{awaiting.proposal.uri}</code>
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
                    {awaiting.proposal && (
                      <Button size="sm" variant="ghost" loading={reviewBusy === "grant-and-continue"}
                              onClick={() => setReviewDialog("grant")}>
                        <KeyRound aria-hidden /> Grant and release
                      </Button>
                    )}
                    {/* Opens the dialog that carries the rule text. It used to
                        post the action with no rule and take a 400 every time. */}
                    <Button size="sm" variant="ghost" loading={reviewBusy === "mint-rule"}
                            onClick={() => setReviewDialog("rule")}>
                      Mint rule and release
                    </Button>
                    <Button size="sm" variant="ghost" loading={reviewBusy === "dismiss"}
                            onClick={() => review("dismiss")}>
                      <Ban aria-hidden /> Dismiss
                    </Button>
                    {/* Cancelling a ticket is destructive and gets a dialog,
                        the same as it does on the Review tab. This was a
                        one-click cancel. */}
                    <Button size="sm" variant="danger-ghost" loading={reviewBusy === "deny"}
                            onClick={() => setConfirmDeny(true)}>
                      <XCircle aria-hidden /> Deny
                    </Button>
                  </Row>
                </Stack>
              </Card>
            </section>
          )}

          {/* Discussion ------------------------------------------------ */}
          <section>
            <SectionLabel icon={<MessageSquare aria-hidden />}>Discussion</SectionLabel>
            <Card>
              <Stack gap="sm">
                {comments.length === 0 ? (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                    Nothing yet. Notes here are for you and anyone reading the ticket later.
                    An answer a worker must act on belongs in the description instead.
                  </span>
                ) : (
                  comments.map((c) => (
                    <div key={c.id} className="ws-comment">
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <span style={{ fontWeight: "var(--fw-semi)", fontSize: "var(--fs-sm)" }}>{c.author}</span>
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }} title={fmtTs(c.ts)}>
                          {ago(c.ts)}
                        </span>
                      </Row>
                      <p className="ws-wrap" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-sm)" }}>
                        {c.text}
                      </p>
                    </div>
                  ))
                )}
                <Field label="Add a comment">
                  <Textarea rows={3} value={commentDraft}
                            onChange={(e) => setCommentDraft(e.target.value)}
                            placeholder="Context, a decision, something the next reader needs." />
                </Field>
                <Row>
                  <Button size="sm" variant="primary" onClick={postComment}
                          loading={commentBusy} disabled={commentBusy || !commentDraft.trim()}>
                    <Send aria-hidden /> Comment
                  </Button>
                </Row>
              </Stack>
            </Card>
          </section>

          {/* Runs ------------------------------------------------------ */}
          <section>
            <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
            <Card>
              {(runs?.length ?? 0) === 0 ? (
                <StateBlock title="No runs yet"
                  hint="Runs appear here every time this ticket is attempted (manually or by an agent sweep)." />
              ) : (
                <Stack gap="sm">
                  {(runs || []).map((r) => {
                    const dur = runDuration(r);
                    return (
                      <Row key={r.id} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        {/* jobStatusKind, not a local ternary. The inlined one
                            here had no awaiting_input case, so a run waiting on
                            the operator rendered grey on this page and amber on
                            every other one. */}
                        <StatusDot status={jobStatusKind(r.status)}>{r.status}</StatusDot>
                        {r.kind && <Badge>{r.kind}</Badge>}
                        {r.trigger && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{r.trigger}</span>}
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}
                              title={fmtTs(r.started_at)}>
                          {[ago(r.started_at), dur].filter(Boolean).join(" · ")}
                        </span>
                        {r.evidence?.summary && (
                          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                            {r.evidence.summary}
                          </span>
                        )}
                        <Link to={`/runs/${encodeURIComponent(r.id)}`} style={{ textDecoration: "none" }}>
                          <Button size="sm" variant="ghost">Open</Button>
                        </Link>
                      </Row>
                    );
                  })}
                </Stack>
              )}
            </Card>
          </section>

          {/* Activity -------------------------------------------------- */}
          {/* `evidence` is where every review note, grant, split record, gate
              verdict and denial reason has always been written, and it was
              rendered nowhere: every answer the operator had ever given a
              ticket was invisible on the ticket. */}
          {evidence.length > 0 && (
            <section>
              <SectionLabel icon={<History aria-hidden />}>Activity</SectionLabel>
              <Card>
                <Stack gap="sm">
                  {evidence.map((e, i) => (
                    <Row key={`${e.ts ?? ""}:${i}`} wrap
                         style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      {e.kind && <Badge>{String(e.kind)}</Badge>}
                      <span className="ws-wrap" style={{ flex: "1 1 16rem", minWidth: 0, fontSize: "var(--fs-sm)" }}>
                        {String(e.ref ?? "")}
                      </span>
                      {e.ts && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}
                              title={fmtTs(String(e.ts))}>{ago(String(e.ts))}</span>
                      )}
                    </Row>
                  ))}
                </Stack>
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

      <ConfirmDialog
        open={confirmDeny}
        onCancel={() => setConfirmDeny(false)}
        onConfirm={() => { setConfirmDeny(false); review("deny"); }}
        title="Deny this ticket?"
        body={<>This cancels the ticket. It can't be undone; the work will not happen. Run history and evidence are kept.</>}
        confirmLabel="Deny and cancel"
      />

      {reviewDialog === "grant" && awaiting?.proposal && (
        <GrantDialog
          proposal={awaiting.proposal}
          count={1}
          onClose={() => setReviewDialog(null)}
          onSubmit={(fields) => { setReviewDialog(null); review("grant-and-continue", fields); }}
        />
      )}
      {reviewDialog === "rule" && (
        <RuleDialog
          ask={awaiting?.ask || awaiting?.question || ""}
          scope={scope}
          onClose={() => setReviewDialog(null)}
          onSubmit={(fields) => { setReviewDialog(null); review("mint-rule", fields); }}
        />
      )}

      {/* The dialog CARRIES the target picker. It used to be gated on
          `deployOpen && deployPick`, with deployPick set only by a Select
          further down the page, so the header's Deploy button opened nothing at
          all until the operator had already chosen a target somewhere else.
          That second Select is gone: it was a duplicate of this button. */}
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
