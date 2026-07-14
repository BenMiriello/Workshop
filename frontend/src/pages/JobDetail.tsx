import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronLeft } from "lucide-react";
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
  type StatusKind,
} from "../ui";

// One Job's detail: its binding, trigger, timing, outcome, and freeform
// evidence. Linked from the Jobs feed, Home attention cards, and an agent's
// job history. Reads GET /api/jobs/{id}.

type Job = {
  id: string;
  agent_id?: string;
  project_slug?: string;
  chat_id?: string;
  trigger?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  exit_code?: number;
  tasks_done?: number;
  tasks_failed?: number;
  evidence?: unknown;
  budget?: Record<string, unknown>;
};

const STATUS_KIND: Record<string, StatusKind> = {
  running: "running",
  done: "ok",
  queued: "stopped",
  cancelled: "stopped",
  awaiting_input: "warn",
  failed: "danger",
};

function statusKind(status?: string): StatusKind {
  return STATUS_KIND[status ?? ""] ?? "stopped";
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

  const back = (
    <Button size="sm" variant="ghost" onClick={() => nav("/jobs")}>
      <ChevronLeft aria-hidden /> Jobs
    </Button>
  );

  const header = (
    <PageHeader
      title="Job"
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
    const ev = evidenceText(job.evidence);
    const budgetEntries = job.budget ? Object.entries(job.budget) : [];
    body = (
      <Stack>
        <section>
          <SectionLabel>Overview</SectionLabel>
          <Card style={{ padding: "var(--space-4)" }}>
            <Stack gap="sm">
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <StatusPill status={statusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                {job.trigger && <Tag hue={catFor(job.trigger)}>{job.trigger}</Tag>}
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
                  <Bot aria-hidden /> <span className="ws-mono">{job.agent_id}</span>
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
