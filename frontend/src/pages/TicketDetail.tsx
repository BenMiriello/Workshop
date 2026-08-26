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
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PRIORITY_BANDS, bandOf, floorOf, priorityLabel } from "../lib/priority";
import { formatDuration, timeAbsolute, timeAgo, shortHttpError, Button, Card, Row, Stack, PageHeader, DetailLayout, SectionLabel, StateBlock, Notice, StatusDot, StatusPill, Skeleton, ConfirmDialog, Textarea, Menu, MenuItem, Tag, catFor } from "@workshop/ui";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ChevronLeft, ChevronDown, RefreshCw, Trash2, ExternalLink, MoreHorizontal,
  Play, CheckCheck, ScrollText, MessageSquare, Link2, Send, Plus, MessagesSquare,
  GitPullRequestArrow,
} from "lucide-react";

import { Markdown } from "@workshop/ui/markdown";
import {
  type Task, type TaskLink, TASK_STATUSES, LATITUDES, TASK_EXECUTIONS, TASK_EFFORTS,
  taskStatusKind, taskStatusLabel, title as titleOf, description as descriptionOf,
  scopeLabel, scopeHref, ticketHref, ticketKey, executionLabel, latitudeDesc,
  latitudeSourceLabel, readinessLine, readinessTone, triageLabel,
} from "../lib/tasks";
import { parseList, unresolvedIds } from "../lib/ticketEdit";
import { decisionRefusal, isOpenChange, type Change } from "../lib/changeReview";
import {
  jobIsLive, jobRefusal, nextAdmissibleText, runHref, runOutcome,
  type JobEvidence,
} from "../lib/jobs";
import { nextWorkParams } from "../lib/workParams";
import {
  EditableBody, EditableTitle, PickEditor, PropRow, TextEditor,
} from "../components/tickets/fields";

// A ticket can only be launched from `todo`.
const RUNNABLE_STATUSES = new Set(["todo"]);

// Five seconds is what every live surface in the app uses while a run is in
// flight. A settled ticket is not polled at all.
const POLL_MS = 5000;

type JobRec = {
  id: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  trigger?: string;
  kind?: string;
  // Who ran it. `/api/tasks/{scope}/{id}/jobs` runs the same `_enrich` the runs
  // feed does, so these two arrive on every run and nothing here read them: the
  // page linked to the run and to the chat, and left the agent as the one party
  // you could not reach. This used to be declared as `agent`, a field the jobs
  // store has never written.
  agent_id?: string;
  agent_name?: string;
  // The whole evidence record, straight off the backend's own model, so the
  // refusal a guard attached to a run is readable here rather than being
  // narrowed away by a hand-written shape. See routes/api_models.JobEvidence.
  evidence?: JobEvidence | null;
};

// The two reads the page draws from, as one value. See the query for why they
// are fetched together rather than as two queries.
type TicketRead = { task: Task; runs: JobRec[] };

/** The run this ticket has in flight, if it has one. */
function liveRun(runs: JobRec[] | null | undefined): JobRec | undefined {
  return (runs ?? []).find((r) => jobIsLive(r.status));
}

// The run row's own link. `.ws-runrow` still lays the row out; the link inside
// it takes the row's ink, no underline, and the growth that used to belong to
// `.ws-runrow__who`, so the duration and the time stay pinned right.
const RUN_LINK = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-3)",
  flex: "1 1 auto",
  minWidth: 0,
  color: "inherit",
  textDecoration: "none",
} as const;
const RUN_AGENT = { flex: "none", fontSize: "var(--fs-sm)" } as const;

const runDuration = (r: JobRec): string => formatDuration(r.started_at, r.ended_at);

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

  // The change this ticket's work is on, if it is on one. Fetched separately
  // and BEST EFFORT: /changes needs the relational store and answers 503
  // without it, and a ticket page must still render on a file-backed scope.
  const [change, setChange] = useState<Change | null>(null);
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

  const qc = useQueryClient();
  // Memoised because every write below patches this exact entry, and a fresh
  // array every render would rebuild that path every render.
  const detailKey = useMemo(() => ["tasks", scope, id, "detail"], [scope, id]);

  // ONE QUERY OVER TWO CALLS, not two queries, because the read this replaces
  // was already all-or-nothing: `Promise.all` plus a throw on either response
  // meant neither the ticket nor its runs was written when either failed, and
  // the page showed one error over the last good copy of both. The failure
  // granularity is the behaviour, so it is preserved.
  //
  // THE CADENCE IS SELF-REFERENTIAL and stays that way. `liveRunId` is derived
  // from `runs`, which is this read's OWN output, so as the poll's `enabled` it
  // could only ever turn itself off: nothing else re-reads, so once the last
  // run settled the page stopped polling until a write path or the operator
  // asked again. That is exactly what a data-driven `refetchInterval` does,
  // with the query mounted throughout, read off the cached payload rather than
  // off a render.
  const q = useQuery({
    queryKey: detailKey,
    queryFn: async (): Promise<TicketRead> => {
      const [tr, jr] = await Promise.all([
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`),
        fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/jobs`),
      ]);
      if (!tr.ok) throw new Error(`ticket: ${shortHttpError(tr.status, tr.statusText, await tr.text().catch(() => ""))}`);
      if (!jr.ok) throw new Error(`runs: ${shortHttpError(jr.status, jr.statusText, await jr.text().catch(() => ""))}`);
      const task = (await tr.json()) as Task;
      const runs = (((await jr.json()).jobs || []) as JobRec[]);
      return { task, runs };
    },
    refetchInterval: (query) => (liveRun(query.state.data?.runs) ? POLL_MS : false),
    // `usePoll` runs a catch-up read on the way back from hidden, but only
    // WHILE ENABLED, and this poll was enabled only while a run was in flight.
    // A settled ticket did not re-read on focus and must not start now. Same
    // predicate as the cadence, off the same payload.
    refetchOnWindowFocus: (query) => !!liveRun(query.state.data?.runs),
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read above.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  const task = q.data?.task ?? null;
  const runs = q.data?.runs ?? null;
  // Nothing has read yet, which is what `useState(true)` plus the `finally`
  // meant: a FAILED read counts as read.
  const loading = q.isPending;
  // Cleared at the START of the next attempt, which is what the `setErr(null)`
  // at the top of the old `load` did, so pressing Retry drops the error region
  // rather than holding it over the read that is answering it.
  const err = !q.isFetching && q.isError
    ? (q.error instanceof Error ? q.error.message : String(q.error))
    : null;
  const { refetch } = q;
  // Returns the promise: every write path awaited the re-read before dropping
  // out of its busy state.
  const load = useCallback(async () => { await refetch(); }, [refetch]);
  // One writer for the cached ticket, used by every route that answers with the
  // updated record instead of making the page re-read. Keeps the runs leg as it
  // was, exactly as `setTask` did.
  const putTask = useCallback((next: Task) => {
    qc.setQueryData<TicketRead>(detailKey, (v) => (v ? { ...v, task: next } : v));
  }, [qc, detailKey]);

  const loadChange = useCallback(async () => {
    try {
      const r = await fetch(`/api/changes?ticket_id=${encodeURIComponent(id)}&limit=20`);
      if (!r.ok) { setChange(null); return; }
      const j = await r.json();
      const open = (j.changes || []).find(isOpenChange) ?? (j.changes || [])[0] ?? null;
      setChange(open);
    } catch {
      // No store, no route, no change: the ticket page is unaffected either way.
      setChange(null);
    }
  }, [id]);

  // The mount read for the ticket IS the query above; the effect that used to
  // do it went with the poll's `immediate: false`. The change lookup is a
  // separate, unpolled, best-effort read and keeps its own.
  useEffect(() => { void loadChange(); }, [loadChange]);

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
      putTask((await r.json()) as Task);
      return true;
    } catch (e) {
      // At the field, not as a page banner: the ticket is right there, so a
      // rejected save is a line beside content (frontend/CLAUDE.md).
      setFieldErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [scope, id, putTask]);

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

  // Deciding on FINISHED work. Two verbs, named for what they do, because one
  // word used to mean both: `approve` on this endpoint releases a BLOCKED ticket
  // back to the queue, and sending it here for a finished one un-accepted
  // committed work. See services/task_decision.
  const decide = useCallback(async (action: "accept" | "send-back") => {
    setAnswerBusy(true);
    setBanner(null);
    try {
      const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, note: answer.trim() || null }),
      });
      // A 409 on `accept` is the MERGE CONFLICT answer, and its sentence names
      // the branch state that has to be fixed. `shortHttpError` drops any body
      // over 200 characters, which turns that into a bare "409 Conflict" and
      // leaves the operator with nothing to act on.
      if (!r.ok) throw new Error(decisionRefusal(r.status, r.statusText, await r.text().catch(() => "")));
      setAnswer("");
      say(action === "accept"
        ? (change ? "Accepted. The branch is merged and the ticket is done." : "Accepted. The ticket is done.")
        : "Sent back. The next attempt reads your note in the ticket body.");
      await load();
      await loadChange();
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setAnswerBusy(false);
    }
  }, [scope, id, answer, change, load, loadChange, say, fail]);

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
      putTask((await r.json()) as Task);
      setCommentDraft("");
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      setCommentBusy(false);
    }
  }, [scope, id, commentDraft, fail, putTask]);

  const links = task?.links;
  const dependsOn = links?.depends_on ?? [];
  const comments = task?.comments ?? [];
  const blocked = task?.status === "blocked";
  // A FINISHED ticket is waiting on a person too, and this page used to offer it
  // nothing: the answer box below was gated on `blocked` alone, so a ticket in
  // `in_review` rendered as an ordinary record with no control anywhere on it.
  // Reported 2026-08-22: "it was unclear if it was actually blocked or needed
  // anything ... I came to infer that maybe this isnt actually blocked since it
  // has a commit and somewhere in there implied it was done? Couldn't tell."
  const finished = task?.status === "in_review";
  const originKey = task?.origin?.context_key ?? null;
  const scopeLink = scopeHref(scope);
  const workHref = `/work?${nextWorkParams(new URLSearchParams(), { scope })}`;

  // A run this ticket started is a background process. The ticket record does
  // not move when the run opens, so without this the status still reads "todo"
  // after a successful launch. Say a run is in flight, and poll until it is not.
  const liveRunId = liveRun(runs)?.id ?? null;

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

  const finishedCard = finished && task ? (
    <section>
      <SectionLabel>Waiting on you</SectionLabel>
      <Card compact>
        <Stack>
          <Notice tone="warn">
            {change
              // WITH A CHANGE, accepting is a merge and the copy has to say so:
              // the work is on a branch and the trunk does not carry it yet.
              // See changes-and-review-2026-08-22.md sec 4.
              ? `The work is finished and sitting on ${change.branch_slug}, unmerged. Read the diff before you decide.`
              : "The work on this ticket is finished. Accept it to mark it done, or send it back with a note saying what to change."}
          </Notice>
          {change && (
            <Row>
              <Button size="sm" onClick={() => nav(`/changes/${encodeURIComponent(change.id)}`)}>
                <GitPullRequestArrow aria-hidden /> Review the change
              </Button>
            </Row>
          )}
          <Textarea
            rows={3}
            value={answer}
            aria-label="What should change"
            placeholder="Only needed if you are sending it back. It is written into the ticket, so the next attempt reads it."
            onChange={(e) => setAnswer(e.target.value)}
          />
          <Row>
            <Button size="sm" variant="primary" loading={answerBusy}
                    onClick={() => void decide("accept")}>
              <CheckCheck aria-hidden /> {change ? "Accept and merge" : "Accept"}
            </Button>
            <Button size="sm" loading={answerBusy} onClick={() => void decide("send-back")}>
              Send it back
            </Button>
          </Row>
        </Stack>
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

  // The latest run's refusal, if that is how it ended. Only the latest: an older
  // refusal is history, and a stack of them would bury the run list this sits
  // above.
  const lastRefusal = jobRefusal(lastRun?.evidence);
  const lastRefusalNext = nextAdmissibleText(lastRefusal?.next_admissible_at);

  const runsCard = (
    <section>
      <div className="ws-sectionhead">
        <SectionLabel icon={<ScrollText aria-hidden />}>Runs</SectionLabel>
        {runsInOrder.length > 0 && (
          <span className="ws-sectionhead__sub">
            {runsInOrder.length} attempt{runsInOrder.length === 1 ? "" : "s"}
            {lastRun?.started_at ? ` · last one ${timeAgo(lastRun.started_at)}` : ""}
          </span>
        )}
        <Link className="ws-sectionhead__link" to="/runs">All runs</Link>
      </div>
      {/* WHY THE LAST RUN DID NOT HAPPEN, on the page the operator opens after
          pressing Run and seeing nothing move. A LINE and not a region: the runs
          are on screen, one thing beside them needs saying. `warn`, because a
          guard refusing a run is the guard working. The sentence is the
          server's, verbatim; nothing is re-worded here. */}
      {lastRefusal && (
        <Notice tone="warn">
          {lastRefusal.reason}
          {lastRefusalNext ? ` ${lastRefusalNext}` : ""}
        </Notice>
      )}
      <Card compact pad0>
        {runsInOrder.length === 0
          ? <div className="ws-runrow__none">No runs yet.</div>
          : (
            <div className="ws-runlist">
              {runsInOrder.map((r) => (
                <div key={r.id} className="ws-runrow">
                  {/* The row is the run; the agent beside it is its own link.
                      The row used to BE the <a>, which left nowhere to put a
                      second one. */}
                  <Link style={RUN_LINK} to={runHref(r.id)}>
                    {/* The run's OUTCOME, not its raw status. A run a budget
                        guard turned away is stored `cancelled`, which is the
                        same word an operator's own cancellation leaves; one
                        helper decides that a refusal reads as `refused`. */}
                    <StatusDot status={runOutcome(r.status, jobRefusal(r.evidence)).kind}>
                      {runOutcome(r.status, jobRefusal(r.evidence)).label}
                    </StatusDot>
                    <span className="ws-ticket-key">{r.id.slice(-6)}</span>
                    <span className="ws-runrow__who">
                      {r.kind || "run"} · attempt {r.attempt}
                    </span>
                  </Link>
                  {r.agent_id && (
                    <Link className="ws-muted" style={RUN_AGENT} to={`/agents/${encodeURIComponent(r.agent_id)}`}>
                      {r.agent_name || r.agent_id}
                    </Link>
                  )}
                  <span className="ws-runrow__dur">{runDuration(r)}</span>
                  <span className="ws-runrow__when">{timeAgo(r.started_at)}</span>
                </div>
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
                  <span className="ws-comment__when" title={timeAbsolute(c.ts)}>{timeAgo(c.ts)}</span>
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
              value={priorityLabel(task.priority) || <span className="ws-muted">none</span>}
              editor={(done) => (
                <PickEditor
                  label="Priority"
                  value={bandOf(task.priority)}
                  done={done}
                  onPick={(v) => { void patch({ priority: floorOf(v) }); }}
                  options={PRIORITY_BANDS.map((b) => ({ value: b, label: b }))}
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
                    ...TASK_EFFORTS.map((v) => ({ value: v, label: v }))]}
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
                  <span title={task.triage.reason || timeAbsolute(task.triage.at)}>
                    {triageLabel(task.triage.decision)}
                  </span>
                }
              />
            )}
            <PropRow
              label="Created"
              editable={false}
              value={<span title={timeAbsolute(task.created)}>{timeAgo(task.created)}</span>}
            />
            <PropRow
              label="Updated"
              editable={false}
              value={<span title={timeAbsolute(task.updated)}>{timeAgo(task.updated)}</span>}
            />
            <PropRow
              label="Ticket"
              editable={false}
              value={<code className="ws-mono" title={task.id}>{ticketKey(task.id)}</code>}
            />
            {/* The code behind this ticket. Present at every status, not only
                while it waits on a decision: a merged ticket's change is how
                you find out what actually landed. */}
            {change && (
              <PropRow
                label="Change"
                editable={false}
                value={
                  <Link to={`/changes/${encodeURIComponent(change.id)}`}>
                    {change.branch_slug}
                  </Link>
                }
              />
            )}
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
            <span title={timeAbsolute(task.updated)}>updated {timeAgo(task.updated)}</span>
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
              {finishedCard}
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
