// One ticket. The standard two-column issue page: the work in the main column,
// its properties in a rail.
//
// What was deleted on 2026-08-12, and why (operator direction: remove the junk,
// and measure rather than guess). Field occupancy across all live tickets:
// `parent` 0%, `awaiting` 0%, `max_attempts` 0%, `relates_to` 3%, and
// `assignee` 100% but the string "operator" on every single one, which is zero
// information. Those editors are still gone; deleting an EDITOR never deletes
// its field, so anything the store holds is still served and still patchable.
//
// What the 2026-08-14 redesign changes: the page-wide edit mode is gone and
// every field commits on its own (see components/tickets/fields.tsx for the
// rule and why it is not a retreat from the Save-for-config law), Runs grew
// attempt numbers and durations, Linked chats became its own section instead of
// a rail row, and comments became a real activity thread rather than a stack of
// rows above a textarea.
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ChevronLeft, ChevronDown, RefreshCw, Trash2, ExternalLink, MoreHorizontal,
  Play, CheckCheck, ScrollText, MessageSquare, Link2, Send, Plus, MessagesSquare,
} from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, DetailLayout, SectionLabel, StateBlock,
  Notice, StatusDot, StatusPill, Skeleton, ConfirmDialog, Textarea, Menu,
  MenuItem, Tag, catFor,
} from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";
import {
  type Task, type TaskLink, TASK_STATUSES, LATITUDES, TASK_EXECUTIONS,
  taskStatusKind, taskStatusLabel, title as titleOf, description as descriptionOf,
  scopeLabel, scopeHref, ticketHref, ticketKey, executionLabel, latitudeDesc,
  latitudeSourceLabel, readinessLine, readinessTone, triageLabel,
} from "../lib/tasks";
import { TICKET_EFFORTS, parseList, unresolvedIds } from "../lib/ticketEdit";
import { jobIsLive, jobStatusKind } from "../lib/jobs";
import { nextWorkParams } from "../lib/workParams";
import {
  EditableBody, EditableTitle, PickEditor, PropRow, TextEditor,
} from "../components/tickets/fields";
import { shortHttpError } from "../lib/httpError";

// A ticket can only be launched from `todo`.
const RUNNABLE_STATUSES = new Set(["todo"]);

type JobRec = {
  id: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  trigger?: string;
  kind?: string;
  agent?: string;
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

// Initials for a comment avatar. An agent slug ("note-triager") and a person
// ("Ben") both have to produce two readable characters.
function initials(author: string): string {
  const parts = (author || "?").split(/[\s\-_.:]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

// One linked ticket, as a row that goes somewhere. The href is built from the
// LINK's own scope, not the current ticket's: the two are often different and
// this page used to send every cross-scope dependency to a 404.
function LinkRow({ link }: { link: TaskLink }) {
  return (
    <Link className="ws-deprow" to={ticketHref(link.scope, link.id)}>
      <span title={taskStatusLabel(link.status)} aria-label={taskStatusLabel(link.status)}>
        <StatusDot status={taskStatusKind(link.status)}>{null}</StatusDot>
      </span>
      <span className="ws-ticket-key">{ticketKey(link.id)}</span>
      <span className="ws-deprow__title">{link.title || link.id}</span>
    </Link>
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
  const [saving, setSaving] = useState(false);
  const [fieldErr, setFieldErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [running, setRunning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerErr, setBannerErr] = useState(false);
  const [answer, setAnswer] = useState("");
  const [answerBusy, setAnswerBusy] = useState(false);
  const [commentDraft, setCommentDraft] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);

  const say = useCallback((text: string) => { setBanner(text); setBannerErr(false); }, []);
  const fail = useCallback((text: string) => { setBanner(text); setBannerErr(true); }, []);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [tr, jr] = await Promise.all([
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`),
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/jobs`),
      ]);
      if (!tr.ok) throw new Error(`ticket: ${shortHttpError(tr.status, tr.statusText, await tr.text().catch(() => ""))}`);
      if (!jr.ok) throw new Error(`runs: ${shortHttpError(jr.status, jr.statusText, await jr.text().catch(() => ""))}`);
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

  // ONE writer for every field on this page. It sends exactly the keys it is
  // given, which is what makes per-field commit safe: a title edit cannot
  // re-push labels and body to a linked tracker (the PATCH route
  // write-throughs on those three), and no field can quietly revert another.
  const patch = useCallback(async (fields: Record<string, unknown>) => {
    setSaving(true);
    setFieldErr(null);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setTask(await r.json());
      return true;
    } catch (e) {
      // At the field, not as a page banner: the ticket is right there, so a
      // rejected save is a line beside content (frontend/CLAUDE.md).
      setFieldErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [scope, id]);

  const approve = useCallback(async () => {
    setApproving(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ run: false }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      nav(`/work?${nextWorkParams(new URLSearchParams(), { scope })}`, { replace: true });
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    }
  }, [scope, id, nav, fail]);

  // Releasing a stuck ticket. ONE action: the answer is written into the ticket
  // body by the endpoint, which is what makes it reach the next attempt, since
  // a worker is handed the ticket text and nothing else.
  const unblock = useCallback(async () => {
    setAnswerBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "approve", note: answer.trim() || null }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
  const blocked = task?.status === "blocked";
  const originKey = task?.origin?.context_key ?? null;
  const scopeLink = scopeHref(scope);
  const workHref = `/work?${nextWorkParams(new URLSearchParams(), { scope })}`;

  // A run this ticket started is a background process. The ticket record does
  // not move when the run opens, so without this the status still reads "todo"
  // after a successful launch. Say a run is in flight, and poll until it is not.
  const liveRunId = (runs || []).find((r) => jobIsLive(r.status))?.id ?? null;
  usePoll(load, 5000, { enabled: !!liveRunId, immediate: false });

  const statusRunnable = RUNNABLE_STATUSES.has(task?.status ?? "");
  const stillOpen = !["done", "cancelled", "parked"].includes(task?.status ?? "");
  const canRun = !!task && !saving && !running && !liveRunId && statusRunnable;
  const dependsMissing = unresolvedIds(task?.depends_on, links?.depends_on);
  // What the ticket does not yet say. Advisory, and it stays advisory: nothing
  // here touches `canRun`. Only `ticket_readiness.blocking()` refuses a run, and
  // that refusal happens server-side where the launch is decided.
  const gaps = task?.readiness?.missing ?? [];

  const description = task ? descriptionOf(task) : "";

  // Runs are numbered by when they STARTED, so "attempt 3" is the third attempt
  // whatever order the endpoint returned them in.
  const runsInOrder = useMemo(() => {
    const list = [...(runs ?? [])];
    list.sort((a, b) =>
      new Date(a.started_at ?? 0).getTime() - new Date(b.started_at ?? 0).getTime());
    return list.map((r, i) => ({ ...r, attempt: i + 1 }));
  }, [runs]);
  const lastRun = runsInOrder[runsInOrder.length - 1];

  // ---- main column ----------------------------------------------------------

  const descriptionCard = task ? (
    <section>
      <SectionLabel>Description</SectionLabel>
      <Card compact>
        <EditableBody
          value={description}
          render={<Markdown>{description}</Markdown>}
          busy={saving}
          error={fieldErr}
          onSave={(next) => { void patch({ body: next }); }}
        />
      </Card>
    </section>
  ) : null;

  const blockCard = blocked && task ? (
    <section>
      <SectionLabel>Stuck</SectionLabel>
      <Card compact>
        <Stack>
          <Notice tone="warn">
            {task.blocked_reason || task.awaiting?.question || "This ticket stopped and has not said why."}
          </Notice>
          <Textarea
            rows={4}
            value={answer}
            aria-label="Your answer"
            placeholder="Answer the question. It is written into the ticket, so the next attempt reads it."
            onChange={(e) => setAnswer(e.target.value)}
          />
          <Row>
            <Button size="sm" variant="primary" loading={answerBusy} onClick={unblock}>
              <CheckCheck aria-hidden /> Answer and unblock
            </Button>
          </Row>
        </Stack>
      </Card>
    </section>
  ) : null;

  const runsCard = (
    <section>
      <div className="ws-sectionhead">
        <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
        {runsInOrder.length > 0 && (
          <span className="ws-sectionhead__sub">
            {runsInOrder.length} attempt{runsInOrder.length === 1 ? "" : "s"}
            {lastRun?.started_at ? ` · last one ${ago(lastRun.started_at)}` : ""}
          </span>
        )}
        <Link className="ws-sectionhead__link" to="/runs">All runs</Link>
      </div>
      <Card compact pad0>
        {runsInOrder.length === 0
          ? <div className="ws-runrow__none">No runs yet.</div>
          : (
            <div className="ws-runlist">
              {runsInOrder.map((r) => (
                <Link key={r.id} className="ws-runrow" to={`/runs/${encodeURIComponent(r.id)}`}>
                  <StatusDot status={jobStatusKind(r.status)}>{r.status}</StatusDot>
                  <span className="ws-ticket-key">{r.id.slice(-6)}</span>
                  <span className="ws-runrow__who">
                    {r.kind || "run"} · attempt {r.attempt}
                  </span>
                  <span className="ws-runrow__dur">{runDuration(r)}</span>
                  <span className="ws-runrow__when">{ago(r.started_at)}</span>
                </Link>
              ))}
            </div>
          )}
      </Card>
    </section>
  );

  // Its own section, not a rail row. A ticket filed from a chat has a
  // conversation behind it that says WHY, and reaching it from a one-line
  // property was how "opened from the inbox, dead end" happened.
  //
  // ONE relation today: `origin`, the chat this was filed from. The design also
  // drew "Referenced in", and nothing indexes that: no store, endpoint or field
  // records a chat mentioning an existing ticket. Rather than draw an empty
  // half, the section shows what exists and the label carries the relation, so
  // adding the other kind later is a row, not a redesign.
  const chatsCard = originKey ? (
    <section>
      <SectionLabel icon={<MessagesSquare aria-hidden />}>Linked chats</SectionLabel>
      <Card compact>
        <div className="ws-chatrow">
          <span className="ws-chatrow__rel">Filed from</span>
          <Link className="ws-chatrow__title" to={`/chat/${encodeURIComponent(originKey)}`}>
            {task?.origin?.title || "the chat"} <ExternalLink size={12} aria-hidden />
          </Link>
        </div>
      </Card>
    </section>
  ) : null;

  const activityCard = (
    <section>
      <SectionLabel icon={<MessageSquare aria-hidden />}>Activity</SectionLabel>
      <Card compact>
        <Stack>
          {comments.length === 0 && (
            <span className="ws-muted">No comments yet.</span>
          )}
          {comments.map((c) => (
            <div key={c.id} className="ws-comment">
              <span className="ws-comment__avatar" aria-hidden>{initials(c.author)}</span>
              <div className="ws-comment__body">
                <div className="ws-comment__head">
                  <span className="ws-comment__who">{c.author}</span>
                  <span className="ws-comment__when" title={fmtTs(c.ts)}>{ago(c.ts)}</span>
                </div>
                <div className="ws-comment__text">{c.text}</div>
              </div>
            </div>
          ))}
          <div className="ws-comment ws-comment--compose">
            <span className="ws-comment__avatar ws-comment__avatar--self" aria-hidden>You</span>
            <div className="ws-comment__body">
              <Textarea
                rows={2}
                value={commentDraft}
                placeholder="Leave a comment"
                aria-label="Leave a comment"
                onChange={(e) => setCommentDraft(e.target.value)}
              />
              <div className="ws-inlineedit__actions">
                <Button
                  size="sm"
                  variant="primary"
                  loading={commentBusy}
                  disabled={!commentDraft.trim()}
                  onClick={postComment}
                >
                  <Send aria-hidden /> Comment
                </Button>
                <span className="ws-inlineedit__hint">
                  Agents working this ticket read comments before the next attempt.
                </span>
              </div>
            </div>
          </div>
        </Stack>
      </Card>
    </section>
  );

  // ---- rail -----------------------------------------------------------------
  //
  // NO Status row here, deliberately, though the design draws one. Status is
  // ONE fact and it gets ONE control: the pill in the header, which is where it
  // is visible before any scrolling. Two controls for one field with different
  // commit rules is exactly how a Save silently reverts a status somebody had
  // already changed (docs/decisions, single canonical status source).
  const labels = task?.labels ?? [];

  const rail = task ? (
    <Stack>
      <section>
        <SectionLabel>Details</SectionLabel>
        <Card compact>
          <div className="ws-props2">
            <PropRow
              label="Priority"
              value={task.priority ? task.priority : <span className="ws-muted">none</span>}
              editor={(done) => (
                <TextEditor
                  label="Priority"
                  inputMode="numeric"
                  value={String(task.priority ?? 0)}
                  done={done}
                  onSave={(v) => { void patch({ priority: Number(v) || 0 }); }}
                />
              )}
            />
            <PropRow
              label="Effort"
              value={task.effort ?? <span className="ws-muted">not set</span>}
              editor={(done) => (
                <PickEditor
                  label="Effort"
                  value={task.effort ?? ""}
                  done={done}
                  onPick={(v) => { void patch({ effort: v }); }}
                  options={[{ value: "", label: "Not set" },
                    ...TICKET_EFFORTS.map((v) => ({ value: v, label: v }))]}
                />
              )}
            />
            {/* Always shown: "not classified" is the value that matters, because
                it is what keeps a ticket out of an unattended sweep, and a row
                that disappears cannot say so. */}
            <PropRow
              label="Execution"
              value={task.execution
                ? executionLabel(task.execution)
                : <span className="ws-muted">not classified</span>}
              editor={(done) => (
                <PickEditor
                  label="Execution"
                  value={task.execution ?? ""}
                  done={done}
                  onPick={(v) => { void patch({ execution: v }); }}
                  options={[{ value: "", label: "Not classified" },
                    ...TASK_EXECUTIONS.map((v) => ({ value: v, label: v }))]}
                />
              )}
            />
            {/* The EFFECTIVE value with its source, because null means inherit:
                a latitude shown without where it came from is how an inherited
                setting gets mistaken for a deliberate one. */}
            <PropRow
              label="Latitude"
              value={
                <span title={latitudeDesc(task.effective_latitude?.value)}>
                  {task.effective_latitude?.value ?? "standard"}
                  <span className="ws-muted">
                    {" · "}{latitudeSourceLabel(task.effective_latitude?.source)}
                  </span>
                </span>
              }
              editor={(done) => (
                <PickEditor
                  label="Latitude"
                  value={task.latitude ?? ""}
                  done={done}
                  onPick={(v) => { void patch({ latitude: v }); }}
                  options={[{ value: "", label: "Inherit from the scope" },
                    ...LATITUDES.map((v) => ({ value: v, label: v }))]}
                />
              )}
            />
            {task.acceptance?.kind && (
              <PropRow
                label="Acceptance"
                editable={false}
                value={<span title={task.acceptance.spec ?? undefined}>{task.acceptance.kind}</span>}
              />
            )}
            {task.triage?.decision && (
              <PropRow
                label="Triage"
                editable={false}
                value={
                  <span title={task.triage.reason || fmtTs(task.triage.at)}>
                    {triageLabel(task.triage.decision)}
                  </span>
                }
              />
            )}
            <PropRow
              label="Created"
              editable={false}
              value={<span title={fmtTs(task.created)}>{ago(task.created)}</span>}
            />
            <PropRow
              label="Updated"
              editable={false}
              value={<span title={fmtTs(task.updated)}>{ago(task.updated)}</span>}
            />
            <PropRow
              label="Ticket"
              editable={false}
              value={<code className="ws-mono" title={task.id}>{ticketKey(task.id)}</code>}
            />
            {/* Tags: pills plus a dashed + , the same affordance as "+ Filter"
                on the list. A tag is free text, so the editor is a text field
                rather than a picker over a vocabulary that does not exist. */}
            <PropRow
              label="Tags"
              value={labels.length
                ? <span className="ws-tagstrip">
                    {labels.map((l) => (
                      <Tag key={l} hue={catFor(l)} variant="neutral">{l}</Tag>
                    ))}
                  </span>
                : <span className="ws-addfilter ws-addfilter--sm"><Plus aria-hidden /> Add</span>}
              editor={(done) => (
                <TextEditor
                  label="Tags, comma separated"
                  value={labels.join(", ")}
                  placeholder="design, infra"
                  done={done}
                  onSave={(v) => { void patch({ labels: parseList(v) }); }}
                />
              )}
            />
          </div>
          {fieldErr && <Notice tone="error">{fieldErr}</Notice>}
        </Card>
      </section>

      <section>
        <div className="ws-sectionhead">
          <SectionLabel icon={<Link2 aria-hidden />}>Dependencies</SectionLabel>
        </div>
        <Card compact>
          <Stack gap="sm">
            {dependsOn.map((l) => <LinkRow key={l.id} link={l} />)}
            {dependsMissing.map((mid) => (
              <span key={mid} className="ws-muted">{ticketKey(mid)} (not found)</span>
            ))}
            <PropRow
              label="Depends on"
              value={dependsOn.length + dependsMissing.length === 0
                ? <span className="ws-addfilter ws-addfilter--sm"><Plus aria-hidden /> Add</span>
                : <span className="ws-muted">Edit the list</span>}
              editor={(done) => (
                <TextEditor
                  label="Depends on, comma separated ticket ids"
                  value={(task.depends_on ?? []).join(", ")}
                  done={done}
                  onSave={(v) => { void patch({ depends_on: parseList(v) }); }}
                />
              )}
            />
          </Stack>
        </Card>
      </section>

      {/* Advisory, and un-carded on purpose: it is a note about handing this to
          a worker, not a property of the ticket. Only while the ticket is still
          work: on a done or cancelled one it would be a warn banner on history.
          It NEVER blocks; `canRun` does not read it. */}
      {gaps.length > 0 && stillOpen && (
        <section className="ws-readiness">
          <SectionLabel>Not yet specified</SectionLabel>
          <p className={`ws-readiness__line${readinessTone(gaps) === "error" ? " is-blocking" : ""}`}>
            {readinessLine(gaps)}
            {readinessTone(gaps) === "error" ? "" : " Advisory: a run will still start."}
          </p>
        </section>
      )}
    </Stack>
  ) : null;

  // ---- page -----------------------------------------------------------------

  return (
    <div>
      <PageHeader
        variant="entity"
        back={<Link to={workHref}><ChevronLeft aria-hidden /> Tickets</Link>}
        title={loading && !task
          ? "Ticket"
          : (
            <EditableTitle
              value={task ? titleOf(task) : id}
              busy={saving}
              error={fieldErr}
              onSave={(next) => { void patch({ title: next.replace(/\r?\n/g, " ").trim() }); }}
            />
          )}
        meta={task && (
          <>
            <code className="ws-mono" title={task.id}>{ticketKey(task.id)}</code>
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
              {/* The status pill IS the control: one click, one pick,
                  committed. The only place status is written on this page. */}
              <Menu
                label="Set status"
                trigger={
                  <button type="button" className="ws-statusbtn" title="Change status" disabled={saving}>
                    <StatusPill status={taskStatusKind(task.status)}>
                      {taskStatusLabel(task.status)}
                    </StatusPill>
                    <ChevronDown aria-hidden />
                  </button>
                }
              >
                {TASK_STATUSES.map((s) => (
                  <MenuItem
                    key={s}
                    checked={s === task.status}
                    onSelect={() => { void patch({ status: s }); }}
                  >
                    {taskStatusLabel(s)}
                  </MenuItem>
                ))}
              </Menu>
              {task.status === "draft" && (
                <Button size="sm" variant="primary" loading={approving} onClick={approve}>
                  <CheckCheck aria-hidden /> Approve
                </Button>
              )}
              {canRun && (
                <Button size="sm" variant="primary" loading={running} onClick={runTask}>
                  <Play aria-hidden /> Run this ticket
                </Button>
              )}
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
            </Row>
          )
        }
      />

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
            <Stack>
              {descriptionCard}
              {blockCard}
              {runsCard}
              {chatsCard}
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
