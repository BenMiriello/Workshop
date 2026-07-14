import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronLeft, HelpCircle } from "lucide-react";
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
import { type Job, jobStatusKind } from "../lib/jobs";

// One Job's detail: its binding, trigger, timing, outcome, and freeform
// evidence. Linked from the Jobs feed, Home attention cards, and an agent's
// job history. Reads GET /api/jobs/{id}.
//
// When a job is awaiting_input its evidence is a list of the tasks it stopped
// on, each carrying a plain-language question. That case gets a dedicated,
// actionable section at the top (approve / send back / unblock / cancel) so the
// operator can see WHAT is waited on, WHERE it ran, and resolve it in place —
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
  const from = job.chat_id ? `, started from chat ${job.chat_id}` : "";
  return `This was ${trig} ${where}${from}`.replace(/\s+/g, " ").trim() + ".";
}

const statusKind = jobStatusKind;

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

function evidenceText(evidence: unknown): string {
  if (evidence == null) return "";
  if (typeof evidence === "string") return evidence;
  try {
    return JSON.stringify(evidence, null, 2);
  } catch {
    return String(evidence);
  }
}

export default function JobDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
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
      nav("/jobs");
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e));
      setActing(null);
    }
  }, [id, nav]);

  const back = (
    <Button size="sm" variant="ghost" onClick={() => nav("/jobs")}>
      <ChevronLeft aria-hidden /> Jobs
    </Button>
  );

  const header = (
    <PageHeader
      title={job?.title || "Job"}
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
        title="Couldn't load job"
        hint={<><code>/api/jobs/{id}</code>. {err}</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (notFound) {
    body = (
      <StateBlock
        title="Job not found"
        hint={<>No job with id <code>{id}</code>. It may have been pruned from the log.</>}
        action={<Button size="sm" onClick={() => nav("/jobs")}>Back to jobs</Button>}
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
    // Only show the raw JSON when it isn't the structured awaiting case (the
    // cards below already render it far more usefully) and isn't a failure (the
    // Error section already renders the reason from the same evidence).
    const ev = (isAwaiting || job.error) ? "" : evidenceText(job.evidence);
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
                <Row style={{ gap: "var(--space-1)", alignItems: "center" }}>
                  <MessageSquare aria-hidden /> <span className="ws-mono">{job.chat_id}</span>
                </Row>
              )}
            </Stack>
          </Card>
        </section>

        {job.error && (
          <section>
            <SectionLabel>Error</SectionLabel>
            <StateBlock variant="error" title="This job failed" hint={job.error} />
          </section>
        )}

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

        {!isAwaiting && (
          <section>
            <SectionLabel>Evidence</SectionLabel>
            {ev ? (
              <Card style={{ padding: "var(--space-3)" }}>
                <pre style={{
                  margin: 0, whiteSpace: "pre-wrap", overflow: "auto", maxHeight: "40vh",
                  fontSize: "var(--fs-sm)",
                }}>{ev}</pre>
              </Card>
            ) : (
              <StateBlock title="No evidence recorded" hint="This job did not attach a proof-of-work payload." />
            )}
          </section>
        )}
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
