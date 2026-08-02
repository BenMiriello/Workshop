import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronLeft, HelpCircle, ListTodo, ListChecks, ArrowUpRight } from "lucide-react";
import {
  Button,
  Card,
  PageHeader,
  Row,
  Stack,
  SectionLabel,
  Skeleton,
  StateBlock,
  StatusPill,
  Tag,
  catFor,
} from "../ui";
import type { StatusKind } from "../ui";
import { type Job, jobStatusKind } from "../lib/jobs";
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

// The task-store scope a job worked in, for a "View task" deep-link into Work.
function scopeForJob(job: Job): string | null {
  if (job.project_slug && job.project_slug !== "workshop") return `project:${job.project_slug}`;
  if (job.project_slug === "workshop") return "workshop";
  if (job.agent_id && job.agent_id.startsWith("agent:")) return job.agent_id;
  return null;
}

function fmtTs(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

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
      <Card style={{ padding: "var(--space-4)" }}>
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
  const [job, setJob] = useState<Job | null>(null);
  const [children, setChildren] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<string | null>(null); // "<taskId>:<action>" in flight
  const [actErr, setActErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setNotFound(false);
    try {
      const r = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
      if (r.status === 404) {
        setNotFound(true);
        setJob(null);
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setJob(await r.json());
      // A coordinator sweep has child Jobs (one per task it ran); fetch them so
      // the sweep detail can nest them. A leaf run simply returns none.
      try {
        const cr = await fetch(`/api/jobs?parent_id=${encodeURIComponent(id)}&limit=100`);
        setChildren(cr.ok ? ((await cr.json()).jobs ?? []) : []);
      } catch {
        setChildren([]);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      nav("/work?tab=runs");
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e));
      setActing(null);
    }
  }, [id, nav]);

  const back = (
    <Button size="sm" variant="ghost" onClick={() => nav("/work?tab=runs")}>
      <ChevronLeft aria-hidden /> Jobs
    </Button>
  );

  const header = (
    <PageHeader
      title={job?.title || "Run"}
      sub={<code className="ws-mono">{id}</code>}
      actions={
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {back}
          <Button size="sm" onClick={refresh} loading={loading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </Row>
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
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (notFound) {
    body = (
      <StateBlock
        title="Run not found"
        hint={<>No job with id <code>{id}</code>. It may have been pruned from the log.</>}
        action={<Button size="sm" onClick={() => nav("/work?tab=runs")}>Back to runs</Button>}
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
                <StateBlock variant="error" title="Action failed" hint={actErr} />
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
        <section>
          <SectionLabel>Overview</SectionLabel>
          <Card style={{ padding: "var(--space-4)" }}>
            <Stack gap="sm">
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <StatusPill status={statusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                {(job.trigger_label || job.trigger) && (
                  <Tag hue={catFor(job.trigger || "")}>{job.trigger_label || job.trigger}</Tag>
                )}
                {dur && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{dur}</span>}
                {typeof job.exit_code === "number" && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>exit {job.exit_code}</span>
                )}
              </Row>
              {job.project_slug && (
                <Row style={{ gap: "var(--space-1)", alignItems: "center", cursor: "pointer" }}
                     onClick={() => nav(`/projects/${encodeURIComponent(job.project_slug!)}`)}>
                  <FolderGit2 aria-hidden /> <span className="ws-mono">{job.project_slug}</span>
                </Row>
              )}
              {job.agent_id && (
                <Row style={{ gap: "var(--space-1)", alignItems: "center", cursor: "pointer" }}
                     onClick={() => nav(`/agents/${encodeURIComponent(job.agent_id!)}`)}>
                  <Bot aria-hidden />
                  <span>{job.agent_name || job.agent_id}</span>
                  {job.agent_name && job.agent_name !== job.agent_id && (
                    <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.agent_id}</span>
                  )}
                </Row>
              )}
              {job.chat_id && (
                job.chat_context_key ? (
                  <Row style={{ gap: "var(--space-1)", alignItems: "center", cursor: "pointer" }}
                       onClick={() => nav(`/chat/${encodeURIComponent(job.chat_context_key!)}?thread=${encodeURIComponent(job.chat_id!)}`)}>
                    <MessageSquare aria-hidden />
                    <span>{job.chat_title || "Chat"}</span>
                    <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.chat_id}</span>
                  </Row>
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
                <Row style={{ gap: "var(--space-1)", alignItems: "center", cursor: "pointer" }}
                     onClick={() => nav(ticketHref(runTicketScope(job), job.task_id!))}>
                  <ListChecks aria-hidden />
                  <span>Linked ticket</span>
                  <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{job.task_id}</span>
                </Row>
              )}
            </Stack>
          </Card>
        </section>

        {children && children.length > 0 && (
          <section>
            <SectionLabel>Child runs</SectionLabel>
            <Card style={{ padding: "var(--space-3)" }}>
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {children.map((c) => {
                  const cscope = scopeForJob(c);
                  return (
                    <li key={c.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <StatusPill status={statusKind(c.status)}>{c.status ?? "unknown"}</StatusPill>
                        <span style={{ flex: "1 1 12rem", wordBreak: "break-word" }}>{c.title || c.task_text || c.id}</span>
                        {cscope && (
                          <button
                            type="button"
                            className="ws-bc__link"
                            style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "inline-flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--fs-xs)" }}
                            onClick={() => nav(`/work?scope=${encodeURIComponent(cscope)}`)}
                          >
                            <ListTodo aria-hidden size={12} /> task
                          </button>
                        )}
                        <button
                          type="button"
                          className="ws-bc__link"
                          style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "inline-flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--fs-xs)" }}
                          onClick={() => nav(`/runs/${encodeURIComponent(c.id)}`)}
                        >
                          View run <ArrowUpRight aria-hidden size={12} />
                        </button>
                      </Row>
                    </li>
                  );
                })}
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
          <Card style={{ padding: "var(--space-4)" }}>
            <Stack gap="sm">
              <Row between wrap><span className="ws-muted">Started</span><span>{fmtTs(job.started_at) || "not started"}</span></Row>
              <Row between wrap><span className="ws-muted">Ended</span><span>{fmtTs(job.ended_at) || "running"}</span></Row>
              {(typeof job.tasks_done === "number" || typeof job.tasks_failed === "number") && (
                <Row between wrap>
                  <span className="ws-muted">Tasks</span>
                  <span>{job.tasks_done ?? 0} done{job.tasks_failed ? `, ${job.tasks_failed} failed` : ""}</span>
                </Row>
              )}
            </Stack>
          </Card>
        </section>

        {budgetEntries.length > 0 && (
          <section>
            <SectionLabel>Budget</SectionLabel>
            <Card style={{ padding: "var(--space-4)" }}>
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
