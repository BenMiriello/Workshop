// One ticket. The standard two-column issue page: the work in the main column,
// its properties in a rail.
//
// What was deleted on 2026-08-12, and why (operator direction: remove the junk,
// and measure rather than guess). Field occupancy across all 179 live tickets:
//
//   parent          0%   -> the Sub-tickets section and the whole split flow
//   awaiting        0%   -> the five-button "Awaiting an answer" panel
//   latitude        0%   -> the Latitude editor and its rail row
//   max_attempts    0%   -> the Attempt cap editor
//   assignee      100% but "operator" on every ticket -> zero information
//   labels        7.3%, and 13 of those are one auto-applied "chat-reported"
//   acceptance    2.8%
//   relates_to    3.4%   -> a decorative edge with no scheduling effect
//
// Also gone: the "Scope" review-and-split card (an LLM judgement pass that could
// only ever act on `parent`/children, which nothing has), the "Not yet
// specified" readiness checklist (a nag panel; approve still reports the gap in
// one line), and the Deploy button and its modal (a project deploy launched from
// a ticket, which is what /deploys is for).
//
// The fields that survived are the ones the data supports: status, priority,
// effort, dependencies, timestamps, origin, runs, comments and activity. The
// deleted EDITORS do not delete their fields: `ticketPatch` sends only what
// changed, so latitude, acceptance, labels and relates_to keep whatever the
// record holds and are still served by the API.
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Ticket as TicketIcon, ArrowLeft, RefreshCw, Trash2, ExternalLink,
  Play, CheckCheck, ScrollText, MessageSquare, History, Link2, Send,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, StatusDot, StatusPill,
  Tag, Skeleton, Field, Input, Select, ConfirmDialog, Textarea,
  catFor,
} from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";
import {
  type Task, type TaskLink, TASK_STATUSES, taskStatusKind, taskStatusLabel,
  title as titleOf, description as descriptionOf, scopeLabel, scopeHref,
  ticketHref,
} from "../lib/tasks";
import {
  EMPTY_TICKET_DRAFT, TICKET_EFFORTS,
  draftFromTask, ticketDirty, ticketPatch, ticketTitleMissing,
  unresolvedIds, type TicketDraft,
} from "../lib/ticketEdit";
import { jobIsLive, jobStatusKind } from "../lib/jobs";
import { nextWorkParams } from "../lib/workParams";

// A ticket can only be launched from `todo`.
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

// One linked ticket, as a row that goes somewhere. The href is built from the
// LINK's own scope, not the current ticket's: the two are often different and
// this page used to send every cross-scope dependency to a 404.
function LinkRow({ link }: { link: TaskLink }) {
  return (
    <Row>
      <StatusDot status={taskStatusKind(link.status)}>{taskStatusLabel(link.status)}</StatusDot>
      <Link to={ticketHref(link.scope, link.id)} className="ws-truncate">{link.title || link.id}</Link>
      <Tag hue={catFor(link.scope)}>{scopeLabel(link.scope)}</Tag>
    </Row>
  );
}

export default function TicketDetail() {
  const { scope: scopeRaw = "", id = "" } = useParams();
  const scope = decodeURIComponent(scopeRaw);
  const nav = useNavigate();

  const [task, setTask] = useState<Task | null>(null);
  const [runs, setRuns] = useState<JobRec[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // ONE draft for the whole ticket. Every field here is ticket CONFIG, so all of
  // it commits on Save and none of it on change (frontend/CLAUDE.md).
  const [draft, setDraft] = useState<TicketDraft>(EMPTY_TICKET_DRAFT);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [running, setRunning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerErr, setBannerErr] = useState(false);
  const [answer, setAnswer] = useState("");
  const [answerBusy, setAnswerBusy] = useState(false);
  const [commentDraft, setCommentDraft] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);

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
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, id]);
  useEffect(() => { load(); }, [load]);

  // What the record says right now, in the form's own vocabulary. The draft is
  // compared against this to decide what changed, so a poll landing while the
  // operator types cannot quietly widen the patch.
  const saved = useMemo(
    () => (task ? draftFromTask(task) : EMPTY_TICKET_DRAFT), [task]);

  useEffect(() => {
    if (task && !editing) setDraft(draftFromTask(task));
  }, [task, editing]);

  const setField = useCallback(
    <K extends keyof TicketDraft>(key: K, value: TicketDraft[K]) =>
      setDraft((d) => ({ ...d, [key]: value })),
    []);

  // Patches the canonical pair, not the joined blob, and sends ONLY the fields
  // that changed (ticketEdit.ts explains why that matters).
  const saveEdits = useCallback(async () => {
    const fields = ticketPatch(draft, saved);
    if (Object.keys(fields).length === 0 || ticketTitleMissing(draft)) return;
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
      // Edit mode closes only on a save that landed. It used to close either
      // way, which threw the operator's edit away on top of the error.
      setEditing(false);
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, saved, scope, id, fail]);

  // A draft is a suggestion the system made. Approving is the operator agreeing
  // to it, and it is the only thing that makes a ticket eligible to run.
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
      if (r.status === 409) {
        const e = await r.json().catch(() => ({} as { detail?: unknown }));
        const detail = typeof e.detail === "string" ? e.detail : "";
        fail(detail || "A sweep is already running against this scope. Wait for it to finish, or stop the queue runner in Settings.");
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      say(j.job_id
        ? "Run started. It appears under Runs below."
        : "The run was accepted but returned no id.");
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [scope, id, load, say, fail]);

  const deleteTask = useCallback(async () => {
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      nav(`/work?${nextWorkParams(new URLSearchParams(), { scope })}`, { replace: true });
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    }
  }, [scope, id, nav, fail]);

  // Releasing a stuck ticket. ONE action, because the five-button panel this
  // replaces (approve / deny / grant-and-continue / mint-rule / dismiss) read a
  // field that is empty on every ticket in the store, so four of its five
  // buttons had never been pressed against real data. The answer is written into
  // the ticket body by the endpoint, which is what makes it reach the next
  // attempt: a worker sees the ticket text and nothing else.
  const unblock = useCallback(async () => {
    setAnswerBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "approve", note: answer.trim() || null }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setAnswer("");
      say("Answered. The next attempt reads it in the ticket body.");
      await load();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setAnswerBusy(false);
    }
  }, [scope, id, answer, load, say, fail]);

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

  const links = task?.links;
  const dependsOn = links?.depends_on ?? [];
  const comments = task?.comments ?? [];
  const evidence = task?.evidence ?? [];
  const blocked = task?.status === "blocked";
  const originKey = task?.origin?.context_key ?? null;
  const scopeLink = scopeHref(scope);

  const workHref = `/work?${nextWorkParams(new URLSearchParams(), { scope })}`;

  // A run this ticket started is a background process. The ticket record does
  // not move when the run opens, so without this the status still reads "To do"
  // after a successful launch. Say a run is in flight, and poll until it is not.
  const liveRunId = (runs || []).find((r) => jobIsLive(r.status))?.id ?? null;
  usePoll(load, 5000, { enabled: !!liveRunId, immediate: false });

  const statusRunnable = RUNNABLE_STATUSES.has(task?.status ?? "");
  const canRun = !!task && !saving && !running && !liveRunId && statusRunnable;
  const dirty = !!task && editing && ticketDirty(draft, saved);
  const titleMissing = editing && ticketTitleMissing(draft);
  const dependsMissing = unresolvedIds(task?.depends_on, links?.depends_on);

  // ---- main column ----------------------------------------------------------

  const description = task ? descriptionOf(task) : "";

  const details = editing ? (
    <Card>
      <Stack>
        <Field label="Title" error={titleMissing ? "A ticket needs a title." : undefined}>
          <Input value={draft.title} onChange={(e) => setField("title", e.target.value)} />
        </Field>
        <Field label="Description">
          <Textarea
            rows={14}
            value={draft.body}
            onChange={(e) => setField("body", e.target.value)}
          />
        </Field>
        <Row wrap>
          <Field label="Status">
            <Select value={draft.status} onChange={(e) => setField("status", e.target.value)}>
              {TASK_STATUSES.map((s) => (
                <option key={s} value={s}>{taskStatusLabel(s)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" help="Higher runs first. 0 is the default.">
            <Input
              inputMode="numeric"
              value={draft.priority}
              onChange={(e) => setField("priority", e.target.value)}
            />
          </Field>
          <Field label="Effort" help="How deep the work is, not how urgent.">
            <Select value={draft.effort} onChange={(e) => setField("effort", e.target.value)}>
              <option value="">Not set</option>
              {TICKET_EFFORTS.map((v) => <option key={v} value={v}>{v}</option>)}
            </Select>
          </Field>
        </Row>
        <Field
          label="Depends on"
          help="Ticket ids, comma separated. This ticket waits for them."
        >
          <Input value={draft.dependsOn} onChange={(e) => setField("dependsOn", e.target.value)} />
        </Field>
      </Stack>
    </Card>
  ) : (
    <Card>
      {description
        ? <Markdown>{description}</Markdown>
        : <span className="ws-muted">No description.</span>}
    </Card>
  );

  const blockCard = blocked && task ? (
    <Stack gap="sm">
      <SectionLabel>Stuck</SectionLabel>
      <Card>
        <Stack>
          <span>{task.blocked_reason || task.awaiting?.question || "This ticket stopped and has not said why."}</span>
          <Field label="Your answer" help="It is written into the ticket, so the next attempt reads it.">
            <Textarea rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          </Field>
          <Row>
            <Button
              size="sm"
              variant="primary"
              loading={answerBusy}
              onClick={unblock}
            >
              <CheckCheck aria-hidden /> Answer and unblock
            </Button>
          </Row>
        </Stack>
      </Card>
    </Stack>
  ) : null;

  const commentsCard = (
    <Stack gap="sm">
      <SectionLabel icon={<MessageSquare aria-hidden />}>Comments</SectionLabel>
      <Card>
        <Stack>
          {comments.length === 0 && <span className="ws-muted">No comments.</span>}
          {comments.map((c) => (
            <Stack key={c.id} gap="sm">
              <Row>
                <strong>{c.author}</strong>
                <span className="ws-muted">{ago(c.ts)}</span>
              </Row>
              <span className="ws-wrap">{c.text}</span>
            </Stack>
          ))}
          <Field label="Add a comment">
            <Textarea
              rows={3}
              value={commentDraft}
              onChange={(e) => setCommentDraft(e.target.value)}
            />
          </Field>
          <Row>
            <Button
              size="sm"
              loading={commentBusy}
              onClick={postComment}
            >
              <Send aria-hidden /> Comment
            </Button>
          </Row>
        </Stack>
      </Card>
    </Stack>
  );

  const runsCard = (
    <Stack gap="sm">
      <Row between>
        <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
        <Link to="/runs">All runs</Link>
      </Row>
      <Card>
        {(runs ?? []).length === 0
          ? <span className="ws-muted">No runs yet.</span>
          : (
            <Stack gap="sm">
              {(runs ?? []).map((r) => (
                <Row between key={r.id}>
                  <Row>
                    <StatusDot status={jobStatusKind(r.status)}>{r.status}</StatusDot>
                    <Link to={`/runs/${encodeURIComponent(r.id)}`}>{r.kind || "run"}</Link>
                  </Row>
                  <span className="ws-muted">
                    {[ago(r.started_at), runDuration(r)].filter(Boolean).join(" · ")}
                  </span>
                </Row>
              ))}
            </Stack>
          )}
      </Card>
    </Stack>
  );

  const activityCard = evidence.length > 0 ? (
    <Stack gap="sm">
      <SectionLabel icon={<History aria-hidden />}>Activity</SectionLabel>
      <Card>
        <Stack gap="sm">
          {evidence.map((e, i) => (
            <Row between key={i}>
              <span className="ws-wrap">
                <strong>{e.kind}</strong>{e.ref ? ` ${e.ref}` : ""}
              </span>
              <span className="ws-muted">{ago(e.ts)}</span>
            </Row>
          ))}
        </Stack>
      </Card>
    </Stack>
  ) : null;

  // ---- rail -----------------------------------------------------------------

  const rail = task ? (
    <Stack gap="sm">
      <SectionLabel>Properties</SectionLabel>
      <Card>
        <Stack>
          <Field label="Status">
            <StatusPill status={taskStatusKind(task.status)}>
              {taskStatusLabel(task.status)}
            </StatusPill>
          </Field>
          <Field label="Scope">
            {/* Not every scope has a page. An unlinkable one renders as the tag
                alone rather than as a link to nowhere. */}
            {scopeLink
              ? <Link to={scopeLink}><Tag hue={catFor(scope || "workshop")}>{scopeLabel(scope)}</Tag></Link>
              : <Tag hue={catFor(scope || "workshop")}>{scopeLabel(scope)}</Tag>}
          </Field>
          {typeof task.priority === "number" && task.priority !== 0 && (
            <Field label="Priority"><span>{task.priority}</span></Field>
          )}
          {task.effort && <Field label="Effort"><span>{task.effort}</span></Field>}
          <Field label="Created"><span title={fmtTs(task.created)}>{ago(task.created)}</span></Field>
          <Field label="Updated"><span title={fmtTs(task.updated)}>{ago(task.updated)}</span></Field>
          {originKey && (
            <Field label="Filed from">
              <Link to={`/chat/${encodeURIComponent(originKey)}`}>
                {task.origin?.title || "the chat"} <ExternalLink aria-hidden />
              </Link>
            </Field>
          )}
        </Stack>
      </Card>
      {(dependsOn.length > 0 || dependsMissing.length > 0) && (
        <>
          <SectionLabel icon={<Link2 aria-hidden />}>Depends on</SectionLabel>
          <Card>
            <Stack gap="sm">
              {dependsOn.map((l) => <LinkRow key={l.id} link={l} />)}
              {dependsMissing.map((mid) => (
                <span key={mid} className="ws-muted">{mid} (not found)</span>
              ))}
            </Stack>
          </Card>
        </>
      )}
    </Stack>
  ) : null;

  // ---- page -----------------------------------------------------------------

  return (
    <div>
      <PageHeader
        title={
          <Row>
            <Link to={workHref} aria-label="Back to tickets">
              <ArrowLeft aria-hidden />
            </Link>
            <TicketIcon aria-hidden />
            <span className="ws-wrap">{loading ? "Ticket" : titleOf(task as Task) || id}</span>
          </Row>
        }
        actions={
          task && (
            <Row wrap>
              {liveRunId && <StatusPill status="running">run in flight</StatusPill>}
              {!editing && (
                <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
              )}
              {editing && (
                <>
                  <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(saved); }}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="primary" loading={saving} disabled={!dirty} onClick={saveEdits}>
                    Save
                  </Button>
                </>
              )}
              {task.status === "draft" && (
                <Button size="sm" variant="primary" loading={approving} onClick={approve}>
                  <CheckCheck aria-hidden /> Approve
                </Button>
              )}
              {canRun && (
                <Button size="sm" loading={running} onClick={runTask}>
                  <Play aria-hidden /> Run
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={load} aria-label="Refresh">
                <RefreshCw aria-hidden />
              </Button>
              <Button
                size="sm"
                variant="danger-ghost"
                onClick={() => setConfirmDelete(true)}
                aria-label="Delete ticket"
              >
                <Trash2 aria-hidden />
              </Button>
            </Row>
          )
        }
      />

      {banner && (
        <StateBlock
          variant={bannerErr ? "error" : undefined}
          title={banner}
          action={<Button size="sm" onClick={() => setBanner(null)}>Dismiss</Button>}
        />
      )}

      {loading && !task && (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="40%" /></Stack></Card>
      )}

      {err && !task && (
        <StateBlock
          variant="error"
          title="Couldn't load this ticket"
          hint={<><code>/api/tasks/{scope}/{id}</code> ({err})</>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      )}

      {task && (
        <Row wrap>
          <Stack gap="lg" style={{ flex: "1 1 420px", minWidth: 0 }}>
            {details}
            {blockCard}
            {commentsCard}
            {runsCard}
            {activityCard}
          </Stack>
          <div style={{ flex: "0 0 260px", minWidth: 240 }}>{rail}</div>
        </Row>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => { setConfirmDelete(false); deleteTask(); }}
        title="Delete this ticket?"
        body="The ticket and its history are removed. This cannot be undone."
        confirmLabel="Delete"
        danger
      />
    </div>
  );
}
