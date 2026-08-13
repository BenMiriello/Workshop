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
  ChevronLeft, ChevronDown, RefreshCw, Trash2, ExternalLink, MoreHorizontal,
  Play, CheckCheck, ScrollText, MessageSquare, History, Link2, Send,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, DetailLayout, Props, Prop, Clamp,
  SectionLabel, StateBlock, Notice, StatusDot, StatusPill,
  Skeleton, Field, Input, Select, ConfirmDialog, Textarea, Menu, MenuItem,
} from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";
import {
  type Task, type TaskLink, TASK_STATUSES, taskStatusKind, taskStatusLabel,
  title as titleOf, description as descriptionOf, scopeLabel, scopeHref,
  ticketHref, executionLabel, readinessLine,
} from "../lib/tasks";
import {
  EMPTY_TICKET_DRAFT, TICKET_EFFORTS, TICKET_EXECUTIONS,
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
      {/* The dot alone. A dependency row is three facts, and giving two of them
          chip treatment left nothing for the eye to land on. */}
      <span title={taskStatusLabel(link.status)} aria-label={taskStatusLabel(link.status)}>
        <StatusDot status={taskStatusKind(link.status)}>{null}</StatusDot>
      </span>
      <Link to={ticketHref(link.scope, link.id)} className="ws-truncate">{link.title || link.id}</Link>
      <span className="ws-muted">{scopeLabel(link.scope)}</span>
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
  const [statusBusy, setStatusBusy] = useState(false);

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

  // Status commits ON CHANGE, not behind Save, and it is the one field on this
  // page that does. It is not config: it is where the work has got to, the same
  // move as dragging a card between columns, and every tracker commits it in
  // one interaction. Behind Edit mode it cost three (Edit, pick, Save) for the
  // action the page exists to support. The rest of the ticket stays on the
  // edit-mode model, because a title or a body is a spec a worker will execute.
  const setStatus = useCallback(async (next: string) => {
    if (!task || next === task.status) return;
    setStatusBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setTask(await r.json());
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setStatusBusy(false);
    }
  }, [task, scope, id, fail]);

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
  // What the ticket does not yet say. Advisory, and it stays advisory: nothing
  // here touches `canRun`. Only `ticket_readiness.blocking()` refuses a run, and
  // that refusal happens server-side where the launch is decided.
  const gaps = task?.readiness?.missing ?? [];

  // ---- main column ----------------------------------------------------------

  const description = task ? descriptionOf(task) : "";

  const details = editing ? (
    <Card compact>
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
        {/* No Status field here. It is one fact, so it gets one control, and
            that control is the header's, which commits on change. Two writers
            with different commit rules is how a Save silently reverts a status
            the operator had already changed. */}
        <Row wrap>
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
          {/* Behind Save, with the title and the body, because it is part of
              the spec a worker executes. Not classified is not code: an
              unclassified ticket is never picked up unattended. */}
          <Field
            label="Execution"
            help="code: a worker can change a tree. judgement: the output is a decision. external: needs a host login or a vendor console."
          >
            <Select
              value={draft.execution}
              onChange={(e) => setField("execution", e.target.value)}
            >
              <option value="">Not classified</option>
              {TICKET_EXECUTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
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
    <Card compact>
      {description
        // Cut to a readable height. A ticket body written by the distiller runs
        // to Objective / Output / Start from / Boundaries, which is four
        // paragraphs, and at full height it pushed the comments, the runs and
        // the activity feed off the bottom of the page. The toggle only appears
        // when there is more to see.
        ? <Clamp max={280}><Markdown>{description}</Markdown></Clamp>
        : <span className="ws-muted">No description.</span>}
    </Card>
  );

  // Every section on this page is <section> + SectionLabel + <Card compact>,
  // which is what the run page next door does. It used to be a `Stack gap="sm"`
  // around the label and the card, and SectionLabel already carries its own
  // 12px bottom margin, so the stack's 8px was added to it: 20px under every
  // label, on top of a 32px gap between sections and 24px of card padding.
  // That is the chunk. Nothing here sets a padding value inline.
  const blockCard = blocked && task ? (
    <section>
      <SectionLabel>Stuck</SectionLabel>
      <Card compact>
        <Stack>
          <Notice tone="warn">
            {task.blocked_reason || task.awaiting?.question || "This ticket stopped and has not said why."}
          </Notice>
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
    </section>
  ) : null;

  const commentsCard = (
    <section>
      <SectionLabel icon={<MessageSquare aria-hidden />}>Comments</SectionLabel>
      <Card compact>
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
          {/* The placeholder is the label. A `Field label` here spent a whole
              row saying what the button beneath it already says. */}
          <Textarea
            rows={3}
            value={commentDraft}
            placeholder="Add a comment"
            aria-label="Add a comment"
            onChange={(e) => setCommentDraft(e.target.value)}
          />
          <Row>
            <Button
              size="sm"
              loading={commentBusy}
              disabled={!commentDraft.trim()}
              onClick={postComment}
            >
              <Send aria-hidden /> Comment
            </Button>
          </Row>
        </Stack>
      </Card>
    </section>
  );

  const runsCard = (
    <section>
      <Row between>
        <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
        <Link to="/runs">All runs</Link>
      </Row>
      <Card compact>
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
    </section>
  );

  const activityCard = evidence.length > 0 ? (
    <section>
      <SectionLabel icon={<History aria-hidden />}>Activity</SectionLabel>
      <Card compact>
        <Stack gap="sm">
          {evidence.map((e, i) => (
            <Row between key={i}>
              {/* No bold on the kind: it is a machine token, and weight here
                  made every activity row shout its least interesting word. */}
              <span className="ws-wrap">
                {e.kind}{e.ref ? <> <code className="ws-mono ws-muted">{e.ref}</code></> : null}
              </span>
              <span className="ws-muted">{ago(e.ts)}</span>
            </Row>
          ))}
        </Stack>
      </Card>
    </section>
  ) : null;

  // ---- rail -----------------------------------------------------------------

  // Read-only record fields, as a property list rather than a column of
  // `Field`s. `Field`'s label is form furniture (13px, --fg, medium) and a rail
  // built out of them reads as a disabled form sitting under the real page,
  // which is exactly how it looked.
  //
  // Status, scope and "updated" are NOT repeated here: they are in the header's
  // meta line, where they are visible before any scrolling. One canonical place
  // per fact (docs/decisions single-canonical-status-source).
  const rail = task ? (
    <Stack>
      <section>
        <SectionLabel>Details</SectionLabel>
        <Card compact>
          <Props>
            {typeof task.priority === "number" && task.priority !== 0 && (
              <Prop label="Priority">{task.priority}</Prop>
            )}
            {task.effort && <Prop label="Effort">{task.effort}</Prop>}
            {/* Always shown, unlike the rows above it, because "not classified"
                is the value that matters: it is what keeps a ticket out of an
                unattended sweep, and a row that disappears cannot say so. A
                Prop, not a Badge: one record on a page is nothing to scan a
                list for. */}
            <Prop label="Execution">
              {task.execution
                ? executionLabel(task.execution)
                : <span className="ws-muted">not classified</span>}
            </Prop>
            <Prop label="Created"><span title={fmtTs(task.created)}>{ago(task.created)}</span></Prop>
            {originKey && (
              <Prop label="Filed from">
                <Link to={`/chat/${encodeURIComponent(originKey)}`}>
                  {task.origin?.title || "the chat"} <ExternalLink size={12} aria-hidden />
                </Link>
              </Prop>
            )}
            <Prop label="Ticket id"><code className="ws-mono">{task.id}</code></Prop>
          </Props>
        </Card>
      </section>
      {(dependsOn.length > 0 || dependsMissing.length > 0) && (
        <section>
          <SectionLabel icon={<Link2 aria-hidden />}>Depends on</SectionLabel>
          <Card compact>
            <Stack gap="sm">
              {dependsOn.map((l) => <LinkRow key={l.id} link={l} />)}
              {dependsMissing.map((mid) => (
                <span key={mid} className="ws-muted">{mid} (not found)</span>
              ))}
            </Stack>
          </Card>
        </section>
      )}
    </Stack>
  ) : null;

  // ---- page -----------------------------------------------------------------

  return (
    <div>
      {/* An entity header: no ticket icon (every record on this route is a
          ticket, so a constant mark says nothing), a titled back link instead
          of a bare arrow, and the facts that used to be in the rail on one
          muted line under the title. */}
      <PageHeader
        variant="entity"
        back={<Link to={workHref}><ChevronLeft aria-hidden /> Tickets</Link>}
        title={loading ? "Ticket" : titleOf(task as Task) || id}
        meta={task && (
          <>
            {/* The status word, not a pill, and it is the control as well as
                the readout: one click, one pick, committed. It reads as text
                until you hover it. */}
            <Menu
              label="Set status"
              trigger={
                <button type="button" disabled={statusBusy} title="Change status">
                  <StatusDot status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusDot>
                  <ChevronDown aria-hidden />
                </button>
              }
            >
              {TASK_STATUSES.map((s) => (
                <MenuItem key={s} checked={s === task.status} onSelect={() => setStatus(s)}>
                  {taskStatusLabel(s)}
                </MenuItem>
              ))}
            </Menu>
            {scopeLink
              ? <Link to={scopeLink}>{scopeLabel(scope)}</Link>
              : <span>{scopeLabel(scope)}</span>}
            <span title={fmtTs(task.updated)}>updated {ago(task.updated)}</span>
          </>
        )}
        actions={
          task && (
            <Row wrap>
              {liveRunId && <StatusPill status="running">run in flight</StatusPill>}
              {editing ? (
                <>
                  <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(saved); }}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="primary" loading={saving} disabled={!dirty} onClick={saveEdits}>
                    Save
                  </Button>
                </>
              ) : (
                <>
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
                  <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
                  {/* Refresh and Delete are not turn-by-turn actions. In a row
                      of five buttons they wrapped onto a second line on a phone
                      and put Delete beside Run. */}
                  <Menu
                    label="More actions"
                    trigger={
                      <Button size="sm" variant="ghost" aria-label="More actions">
                        <MoreHorizontal aria-hidden />
                      </Button>
                    }
                  >
                    <MenuItem icon={<RefreshCw aria-hidden />} onSelect={load}>Refresh</MenuItem>
                    <MenuItem danger icon={<Trash2 aria-hidden />} onSelect={() => setConfirmDelete(true)}>
                      Delete ticket
                    </MenuItem>
                  </Menu>
                </>
              )}
            </Row>
          )
        }
      />

      {/* What the ticket does not say, under the Run button that would hand it
          to a worker. ONE LINE OF PROSE. The checklist that used to live on
          this page was deleted on 2026-08-12 as a nag panel, and a checklist in
          different markup is the same panel.

          A `Notice`, not a `StateBlock`: the ticket is right there, so this is
          a line beside content, not a region where content is absent.

          It NEVER blocks. `canRun` does not read it. Above the banner because
          this is standing state and the banner is a transient reply; the other
          order makes the advisory jump every time a save answers. */}
      {gaps.length > 0 && (
        <Notice tone="warn">{readinessLine(gaps)}</Notice>
      )}

      {/* A LINE, not a region: the ticket is still on screen, and a rejected
          save is one sentence beside it (frontend/CLAUDE.md, "A region or a
          line"). This was a 120px dashed panel wedged between the header and
          the body. */}
      {banner && (
        <Notice
          tone={bannerErr ? "error" : "info"}
          action={<Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>}
        >
          {banner}
        </Notice>
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
        <DetailLayout
          rail={rail}
          main={
            // Default gap (16px). "lg" is 32px, and with SectionLabel's own
            // 12px under every heading it put ~44px of air between a card and
            // the next section's first line.
            <Stack>
              {details}
              {blockCard}
              {commentsCard}
              {runsCard}
              {activityCard}
            </Stack>
          }
        />
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
