import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { StatusKind, shortHttpError, timeAbsolute, Button, Card, PageHeader, Row, Stack, SectionLabel, Skeleton, StateBlock, StatusPill, StatusDot, Notice } from "@workshop/ui";
import { Link, useParams, useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronLeft, HelpCircle, ListChecks } from "lucide-react";

import {
  type Job, jobIsLive, jobRefusal, jobStatusKind, nextAdmissibleText, runHref,
  runOutcome,
} from "../lib/jobs";
import { ticketHref, runTicketScope } from "../lib/tasks";

// One RUN's detail: its binding, trigger, timing, outcome, and freeform
// evidence. Linked from Work's Runs tab, Home attention cards, and an agent's
// run history. Reads GET /api/jobs/{id}.
//
// "Run" in the UI, `job` in the code: ADR 0001 keeps the code noun, and the
// entity model (section 5) fixes the word the operator reads. Renaming imports
// and CSS hooks would be churn with no reader-facing benefit.
//
// When a run is awaiting_input its evidence is a list of the tasks it stopped
// on, each carrying a plain-language question. That case gets a dedicated,
// actionable section at the top (approve / send back / unblock / cancel) so the
// operator can see WHAT is waited on, WHERE it ran, and resolve it in place -
// not just read a raw JSON blob.

// Five seconds is what every live surface in the app uses while something is
// actually running.
const POLL_MS = 5000;

type WaitingTask = {
  id: string;
  status?: string;
  text?: string;
  blocked_reason?: string | null;
  question?: string;
};

// A job's evidence is "waiting tasks" when it's a list of task-shaped objects.
function waitingTasks(job: Job): WaitingTask[] {
  const ev = job.evidence;
  if (!Array.isArray(ev)) return [];
  return ev.filter(
    (e): e is WaitingTask => !!e && typeof e === "object" && typeof (e as WaitingTask).id === "string",
  );
}

// Plain-language sentence for who/what launched this run.
function ranSummary(job: Job): string {
  const trig =
    job.trigger === "loop" ? "an autonomous sweep"
    : job.trigger === "cron" ? "a scheduled sweep"
    : job.trigger === "event" ? "an event-triggered run"
    : "a manual run";
  const who = job.agent_name || job.agent_id;
  const where =
    job.project_slug ? `of project ${job.project_slug}`
    : who ? `by ${who}`
    : "";
  const from = job.chat_id ? `, started from chat "${job.chat_title || job.chat_id}"` : "";
  return `This was ${trig} ${where}${from}`.replace(/\s+/g, " ").trim() + ".";
}

const statusKind = jobStatusKind;

function duration(job: Job): string {
  if (!job.started_at) return "";
  const start = new Date(job.started_at).getTime();
  const end = job.ended_at ? new Date(job.ended_at).getTime() : NaN;
  if (isNaN(start) || isNaN(end)) return "";
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

// The structured work-product receipt project_ralph.sh attaches to a child Job's
// evidence (files/commits/typecheck/summary). Rendered cleanly instead of the raw
// JSON fallback. Shape mirrors scripts/project_ralph.sh build_receipt_evidence.
type Receipt = {
  kind: "receipt";
  files?: string[];
  commits?: string[];
  baseline?: string;
  head?: string;
  diff_files?: number;
  typecheck?: Record<string, string>;
  design?: string;
  iterations?: number;
  tokens?: number;
  summary?: string;
};

function asReceipt(evidence: unknown): Receipt | null {
  // The jobs finish CLI stores the --evidence argument verbatim, so evidence can
  // arrive either as a real object OR as a JSON-encoded string. Accept both so
  // the receipt renders no matter which form the store holds.
  let ev: unknown = evidence;
  if (typeof ev === "string") {
    const s = ev.trim();
    if (!s.startsWith("{")) return null;
    try {
      ev = JSON.parse(s);
    } catch {
      return null;
    }
  }
  if (
    !!ev &&
    typeof ev === "object" &&
    !Array.isArray(ev) &&
    (ev as { kind?: unknown }).kind === "receipt"
  ) {
    return ev as Receipt;
  }
  return null;
}

// A gate result (ok/fail/skipped) mapped onto the five design status kinds.
function checkKind(v?: string): StatusKind {
  return v === "ok" ? "ok" : v === "fail" ? "danger" : "stopped";
}

const FILES_SHOWN = 50; // overflow cap for the changed-files list

function ReceiptView({ receipt }: { receipt: Receipt }) {
  const files = receipt.files ?? [];
  const shown = files.slice(0, FILES_SHOWN);
  const overflow = files.length - shown.length;
  const commits = receipt.commits ?? [];
  const checks = receipt.typecheck ? Object.entries(receipt.typecheck) : [];
  return (
    <section>
      <SectionLabel>Work product</SectionLabel>
      <Card compact>
        <Stack gap="sm">
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            {checks.map(([k, v]) => (
              <StatusPill key={k} status={checkKind(v)}>{`${k} ${v}`}</StatusPill>
            ))}
            {receipt.design && (
              <StatusPill status={checkKind(receipt.design)}>{`design ${receipt.design}`}</StatusPill>
            )}
          </Row>
          <Row wrap style={{ gap: "var(--space-3)" }}>
            {typeof receipt.diff_files === "number" && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                {receipt.diff_files} file{receipt.diff_files === 1 ? "" : "s"} changed
              </span>
            )}
            {typeof receipt.iterations === "number" && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                {receipt.iterations} iteration{receipt.iterations === 1 ? "" : "s"}
              </span>
            )}
            {typeof receipt.tokens === "number" && receipt.tokens > 0 && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                {receipt.tokens.toLocaleString()} tokens
              </span>
            )}
          </Row>

          {receipt.summary && (
            <p style={{ margin: 0, fontSize: "var(--fs-sm)", whiteSpace: "pre-wrap" }}>
              {receipt.summary}
            </p>
          )}

          {files.length > 0 && (
            <Stack gap="sm">
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Changed files</span>
              <Stack gap="sm">
                {shown.map((f) => (
                  <span key={f} className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{f}</span>
                ))}
              </Stack>
              {overflow > 0 && (
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  and {overflow} more file{overflow === 1 ? "" : "s"}
                </span>
              )}
            </Stack>
          )}

          {commits.length > 0 && (
            <Stack gap="sm">
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Commits</span>
              <Row wrap style={{ gap: "var(--space-2)" }}>
                {commits.map((c) => (
                  <span key={c} className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{c.slice(0, 7)}</span>
                ))}
              </Row>
            </Stack>
          )}
        </Stack>
      </Card>
    </section>
  );
}

export default function JobDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [acting, setActing] = useState<string | null>(null); // "<taskId>:<action>" in flight
  const [actErr, setActErr] = useState<string | null>(null);
  // THE MANUAL READ CARRIES ITS OWN FLAG, and that is the whole of what the old
  // `quiet` parameter did. One function served both the button and the poll and
  // took a boolean saying which it was, because the polled read must not flash
  // the Refresh spinner every five seconds. `isFetching` cannot tell them
  // apart, so the button says so itself; the mount read is `isLoading`, which
  // is the one automatic read that DID show the spinner.
  const [manual, setManual] = useState(false);

  // A run is a background process: it changes without us acting. This page used
  // to read only on mount and on the manual button, so a run opened while in
  // flight said "running" and "Ended: running" forever, however long ago it had
  // actually finished. Poll while it can still move, and stop the moment it
  // cannot (jobIsLive), so a finished run costs nothing. Read off the cached
  // payload rather than off a render, so the cadence and the page cannot
  // describe different runs.
  const jobQuery = useQuery({
    queryKey: ["jobs", "detail", id],
    queryFn: async (): Promise<Job | null> => {
      const r = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
      // A PRUNED RUN IS AN ANSWER, not a failure: it gets its own region, so it
      // must not land on the error path.
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return (await r.json()) as Job;
    },
    refetchInterval: (query) => (jobIsLive(query.state.data?.status) ? POLL_MS : false),
    // `usePoll` runs a catch-up read on the way back from hidden, but only
    // WHILE ENABLED, and this poll was enabled only for a live run. A settled
    // run did not re-read on focus and must not start now. Read off the cached
    // status, so a run that finished while the tab was away is still caught.
    refetchOnWindowFocus: (query) => jobIsLive(query.state.data?.status),
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read above.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  const job = jobQuery.data ?? null;
  const live = jobIsLive(job?.status);

  // A coordinator sweep has child Jobs (one per task it ran); a leaf run simply
  // returns none. SEQUENTIAL AND DEPENDENT in the code this replaces: the child
  // list was only ever read after the run itself came back OK, so a 404 or a
  // failed read left the previous children alone rather than clearing them.
  // `enabled` is that dependency; a disabled query keeps its cached list, which
  // is the same thing the early `return` did.
  //
  // WHAT CHANGES: two queries on one cadence are scheduled independently, so
  // the pair is no longer strictly ordered within a tick. Nothing reads one
  // against the other -- the children render as their own section -- so the
  // page is the same; the request pattern is two timers instead of one.
  const childrenQuery = useQuery({
    queryKey: ["jobs", "children", id],
    queryFn: async (): Promise<Job[]> => {
      // A failed child read was swallowed into an empty list, never onto the
      // page's error region. Unchanged.
      try {
        const cr = await fetch(`/api/jobs?parent_id=${encodeURIComponent(id)}&limit=100`);
        return cr.ok ? (((await cr.json()).jobs ?? []) as Job[]) : [];
      } catch {
        return [];
      }
    },
    enabled: jobQuery.isSuccess && jobQuery.data !== null,
    refetchInterval: live ? POLL_MS : false,
    refetchOnWindowFocus: live,
    staleTime: 0,
    retry: false,
  });
  const children = childrenQuery.data ?? null;

  // Cleared at the START of the next attempt, which is what `setErr(null)` and
  // `setNotFound(false)` at the top of the old `refresh` did, for the poll as
  // well as for the button.
  const err = !jobQuery.isFetching && jobQuery.isError
    ? (jobQuery.error instanceof Error ? jobQuery.error.message : String(jobQuery.error))
    : null;
  const notFound = !jobQuery.isFetching && jobQuery.isSuccess && jobQuery.data === null;
  const loading = manual || jobQuery.isLoading;

  const { refetch: refetchJob } = jobQuery;
  const { refetch: refetchChildren } = childrenQuery;
  const refresh = useCallback(async () => {
    setManual(true);
    try {
      await refetchJob();
      await refetchChildren();
    } finally {
      setManual(false);
    }
  }, [refetchJob, refetchChildren]);

  const act = useCallback(
    async (taskId: string, action: string) => {
      setActing(`${taskId}:${action}`);
      setActErr(null);
      try {
        const r = await fetch(`/api/jobs/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}/action`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action }),
        });
        if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        await refresh();
      } catch (e) {
        setActErr(e instanceof Error ? e.message : String(e));
      } finally {
        setActing(null);
      }
    },
    [id, refresh],
  );

  const dismiss = useCallback(async () => {
    setActing("__dismiss__");
    setActErr(null);
    try {
      const r = await fetch(`/api/jobs/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      nav("/runs");
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e));
      setActing(null);
    }
  }, [id, nav]);

  // Back is navigation, so it sits above the title as a named link, not as a
  // button in the action row where it competed with Refresh. "Runs", not
  // "Jobs": the route is /runs and `job` is the code noun only (ADR 0001).
  const header = (
    <PageHeader
      variant="entity"
      back={<Link to="/runs"><ChevronLeft aria-hidden /> Runs</Link>}
      title={job?.title || "Run"}
      meta={job && (
        <>
          {/* The OUTCOME, not the raw status: a run a guard refused is stored
              `cancelled`, which is also what an operator's own cancellation
              leaves. One helper decides that a refusal reads as `refused`. */}
          <StatusDot status={runOutcome(job.status, jobRefusal(job.evidence)).kind}>
            {runOutcome(job.status, jobRefusal(job.evidence)).label}
          </StatusDot>
          {job.started_at && <span>started {timeAbsolute(job.started_at)}</span>}
          {duration(job) && <span>{duration(job)}</span>}
          <code className="ws-mono">{id}</code>
        </>
      )}
      actions={
        <Button size="sm" onClick={() => void refresh()} loading={loading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      }
    />
  );

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load run"
        hint={<><code>/api/jobs/{id}</code>. {err}</>}
        action={<Button size="sm" onClick={() => void refresh()}>Retry</Button>}
      />
    );
  } else if (notFound) {
    body = (
      <StateBlock
        title="Run not found"
        hint={<>No job with id <code>{id}</code>. It may have been pruned from the log.</>}
        action={<Button size="sm" onClick={() => nav("/runs")}>Back to runs</Button>}
      />
    );
  } else if (!job) {
    body = (
      <Stack>
        <Skeleton width="40%" />
        <Skeleton width="60%" />
        <Skeleton width="30%" />
      </Stack>
    );
  } else {
    const dur = duration(job);
    // What a guard said when it turned this run away, if that is how it ended.
    const refusal = jobRefusal(job.evidence);
    const refusalNext = nextAdmissibleText(refusal?.next_admissible_at);
    const waiting = waitingTasks(job);
    const isAwaiting = job.status === "awaiting_input" && waiting.length > 0;
    const receipt = asReceipt(job.evidence);
    // Only show the raw JSON when it isn't the structured awaiting case, isn't a
    // structured receipt (both render far more usefully below), and isn't a
    // failure (the Error section already renders the reason from the evidence).
    // A failed run still carries a receipt, so ReceiptView renders regardless of
    // job.error; only the generic <pre> is suppressed on failure.
    // Evidence JSON dump retired in C14; the ev variable is gone with it.
    const budgetEntries = job.budget ? Object.entries(job.budget) : [];
    body = (
      <Stack>
        {isAwaiting && (
          <section>
            <SectionLabel>Needs your input</SectionLabel>
            <Stack gap="sm">
              <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
                {ranSummary(job)}{" "}
                {job.project_slug || job.agent_id
                  ? "It paused because these tasks need you before the sweep can continue. Resolve one and the loop picks it up on its next run."
                  : "It is not bound to any project or agent, so there is nothing to resolve in place (a malformed or orphaned run). Dismiss it to clear this alert."}
              </p>
              {actErr && (
                <Notice tone="error" detail={actErr}>That action failed.</Notice>
              )}
              <Row wrap style={{ gap: "var(--space-2)" }}>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={dismiss}
                  loading={acting === "__dismiss__"}
                  disabled={!!acting && acting !== "__dismiss__"}
                >
                  Dismiss this alert
                </Button>
              </Row>
              {waiting.map((t) => {
                const inReview = t.status === "in_review";
                const busy = (a: string) => acting === `${t.id}:${a}`;
                const anyBusy = acting?.startsWith(`${t.id}:`) ?? false;
                return (
                  <Card key={t.id} style={{ padding: "var(--space-4)" }}>
                    <Stack gap="sm">
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <StatusPill status={inReview ? "warn" : "danger"}>
                          {t.status ?? "waiting"}
                        </StatusPill>
                        <span style={{ fontWeight: 600 }}>{t.text || "(untitled task)"}</span>
                      </Row>
                      <Row style={{ gap: "var(--space-2)", alignItems: "flex-start" }}>
                        <HelpCircle aria-hidden style={{ marginTop: 2, flexShrink: 0 }} />
                        <span style={{ fontSize: "var(--fs-sm)" }}>
                          {t.question || t.blocked_reason || "Waiting on your input."}
                        </span>
                      </Row>
                      <Row wrap style={{ gap: "var(--space-2)" }}>
                        {inReview ? (
                          <>
                            <Button size="sm" onClick={() => act(t.id, "approve")} loading={busy("approve")} disabled={anyBusy}>
                              Approve
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => act(t.id, "reject")} loading={busy("reject")} disabled={anyBusy}>
                              Send back
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" onClick={() => act(t.id, "unblock")} loading={busy("unblock")} disabled={anyBusy}>
                              Unblock
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => act(t.id, "cancel")} loading={busy("cancel")} disabled={anyBusy}>
                              Cancel task
                            </Button>
                          </>
                        )}
                        {(job.project_slug || job.agent_id) && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              nav(
                                job.project_slug
                                  ? `/projects/${encodeURIComponent(job.project_slug)}`
                                  : `/agents/${encodeURIComponent(job.agent_id!)}`,
                              )
                            }
                          >
                            Open {job.project_slug ? "project" : "agent"}
                          </Button>
                        )}
                      </Row>
                    </Stack>
                  </Card>
                );
              })}
            </Stack>
          </section>
        )}
        {/* A GUARD REFUSED THIS RUN, which is not a failure and not an operator
            cancellation. A LINE, not a region: the run record is on screen and
            one thing beside it needs saying, and `warn` rather than `danger`
            because nothing is broken. The sentence is composed once, on the
            server, and printed here verbatim. */}
        {refusal && (
          <Notice tone="warn">
            {refusal.reason}
            {refusalNext ? ` ${refusalNext}` : ""}
          </Notice>
        )}

        <section>
          <SectionLabel>Overview</SectionLabel>
          <Card compact>
            <Stack gap="sm">
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <StatusPill status={runOutcome(job.status, refusal).kind}>
                  {runOutcome(job.status, refusal).label}
                </StatusPill>
                {/* Plain text. This was `<Tag hue={catFor(job.trigger)}>`, which
                    the design system explicitly forbids: a run's trigger is
                    AGENCY, and catFor is the categorical scale. It was also the
                    second chip in a row that already had the status pill. */}
                {(job.trigger_label || job.trigger) && (
                  <span className="ws-muted">{job.trigger_label || job.trigger}</span>
                )}
                {dur && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{dur}</span>}
                {typeof job.exit_code === "number" && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>exit {job.exit_code}</span>
                )}
                {live && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    rechecked every 5 seconds
                  </span>
                )}
              </Row>
              {/* Real links, the shape TicketDetail's dependency rows use. These
                  were `Row onClick` with an inline pointer cursor, so a
                  cross-link could not be middle-clicked, tabbed to, or long
                  pressed, and hovering one told you nothing about where it
                  went. `ws-ticket-link` is the kit's whole-row link. */}
              {job.project_slug && (
                <Link className="ws-ticket-link" to={`/projects/${encodeURIComponent(job.project_slug)}`}>
                  <FolderGit2 aria-hidden /> <span className="ws-mono">{job.project_slug}</span>
                </Link>
              )}
              {job.agent_id && (
                <Link className="ws-ticket-link" to={`/agents/${encodeURIComponent(job.agent_id)}`}>
                  <Bot aria-hidden />
                  <span>{job.agent_name || job.agent_id}</span>
                  {job.agent_name && job.agent_name !== job.agent_id && (
                    <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.agent_id}</span>
                  )}
                </Link>
              )}
              {job.chat_id && (
                job.chat_context_key ? (
                  <Link
                    className="ws-ticket-link"
                    to={`/chat/${encodeURIComponent(job.chat_context_key)}?thread=${encodeURIComponent(job.chat_id)}`}
                  >
                    <MessageSquare aria-hidden />
                    <span>{job.chat_title || "Chat"}</span>
                    <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.chat_id}</span>
                  </Link>
                ) : (
                  <Row style={{ gap: "var(--space-1)", alignItems: "center" }}>
                    <MessageSquare aria-hidden />
                    <span>{job.chat_title || "Chat"}</span>
                    <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.chat_id}</span>
                  </Row>
                )
              )}
              {/* Linked ticket (C14). Prior UI showed job.task_status as a
                  second status pill next to job.status. That created a "which
                  one is the truth?" ambiguity; a run has one status, and the
                  ticket has its own. Link to the ticket instead. */}
              {job.task_id && (
                <Link className="ws-ticket-link" to={ticketHref(runTicketScope(job), job.task_id)}>
                  <ListChecks aria-hidden />
                  <span>Linked ticket</span>
                  <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.task_id}</span>
                </Link>
              )}
            </Stack>
          </Card>
        </section>

        {children && children.length > 0 && (
          <section>
            <SectionLabel>Child runs</SectionLabel>
            <Card pad0>
              <ul className="ws-list">
                {children.map((c) => (
                  <li key={c.id}>
                    <Link className="ws-listrow" to={runHref(c.id)}>
                      <span
                        className="ws-listrow__mark"
                        title={c.status ?? "unknown"}
                        aria-label={c.status ?? "unknown"}
                      >
                        <StatusDot status={statusKind(c.status)}>{null}</StatusDot>
                      </span>
                      <span className="ws-listrow__title">
                        {c.title || c.task_text || c.id}
                      </span>
                      <span className="ws-listrow__meta">
                        <span className="ws-listrow__time">{c.status ?? ""}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          </section>
        )}

        {/* Render this whenever the run FAILED, not only when it carried an error
            string. A failed job with no detail used to render nothing at all,
            which is how a run could report that it errored and then tell you
            precisely nothing about it. Say what is known, say plainly when
            nothing was captured, and give somewhere to go next: a failed run is
            a dead end unless you can get back to what asked for it. */}
        {(job.status === "failed" || job.error) && (
          <section>
            <SectionLabel>Error</SectionLabel>
            <StateBlock
              variant="error"
              title="This run failed"
              hint={
                <Stack gap="sm">
                  <span>
                    {job.error
                      || "No reason was captured for this failure. The run ended without recording an error, so the evidence and timing below are all there is."}
                  </span>
                  <Row wrap style={{ gap: "var(--space-2)" }}>
                    {job.chat_id && job.chat_context_key && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => nav(`/chat/${encodeURIComponent(job.chat_context_key!)}?thread=${encodeURIComponent(job.chat_id!)}`)}
                      >
                        <MessageSquare aria-hidden /> Follow up in the chat that started this
                      </Button>
                    )}
                    {job.task_id && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => nav(ticketHref(runTicketScope(job), job.task_id!))}
                      >
                        <ListChecks aria-hidden /> Open the ticket
                      </Button>
                    )}
                  </Row>
                </Stack>
              }
            />
          </section>
        )}

        {receipt && <ReceiptView receipt={receipt} />}

        <section>
          <SectionLabel>Timing</SectionLabel>
          <Card compact>
            <Stack gap="sm">
              <Row between wrap><span className="ws-muted">Started</span><span>{timeAbsolute(job.started_at) || "not started"}</span></Row>
              {/* Never the bare word "running": a run with no end time is
                  either still going or it died without recording one, and those
                  are different facts. */}
              <Row between wrap>
                <span className="ws-muted">Ended</span>
                <span>
                  {timeAbsolute(job.ended_at) || (live ? "still running" : "no end time was recorded")}
                </span>
              </Row>
              {(typeof job.tasks_done === "number" || typeof job.tasks_failed === "number") && (
                <Row between wrap>
                  <span className="ws-muted">Tasks</span>
                  <span>{job.tasks_done ?? 0} done{job.tasks_failed ? `, ${job.tasks_failed} failed` : ""}</span>
                </Row>
              )}
              {/* From the usage ledger, not from the worker's own receipt. The
                  receipt line below reports what the worker said it spent and
                  only exists when there is a receipt; this is what was actually
                  billed, and it includes the child runs. A coordinator sweep
                  spends almost nothing itself, so its own figure alone would
                  read as "this sweep was free". */}
              {typeof job.tokens === "number" && job.tokens > 0 && (
                <Row between wrap>
                  <span className="ws-muted">Tokens</span>
                  <span title={typeof job.tokens_own === "number" && job.tokens_own !== job.tokens
                    ? `${job.tokens_own.toLocaleString()} in this run, the rest in its child runs`
                    : undefined}>
                    {job.tokens.toLocaleString()}
                  </span>
                </Row>
              )}
            </Stack>
          </Card>
        </section>

        {budgetEntries.length > 0 && (
          <section>
            <SectionLabel>Budget</SectionLabel>
            <Card compact>
              <Stack gap="sm">
                {budgetEntries.map(([k, v]) => (
                  <Row key={k} between wrap>
                    <span className="ws-muted">{k}</span>
                    <span className="ws-mono">{String(v)}</span>
                  </Row>
                ))}
              </Stack>
            </Card>
          </section>
        )}

        {/* Evidence JSON dump retired in C14: it was a raw JSON blob with no
            mechanical consumer. Anything the operator needs is now surfaced
            through the linked ticket, the receipt, or the child runs list. */}
      </Stack>
    );
  }

  return (
    <div>
      {header}
      {body}
    </div>
  );
}
