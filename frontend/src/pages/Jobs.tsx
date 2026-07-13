import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Bot, FolderGit2, MessageSquare } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Row,
  Stack,
  Skeleton,
  StateBlock,
  StatusPill,
  Tag,
  Segmented,
  catFor,
  type StatusKind,
  type SegmentedOption,
} from "../ui";

// The Jobs page: one feed for every bounded execution (cron task, drained
// event, project sweep, supervisor heartbeat, manual run). Reads /api/jobs
// (services.jobs). Interactive chat turns are NOT Jobs and never appear here.

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
};

type JobsResp = { jobs: Job[]; count: number };

type StatusFilter = "all" | "running" | "done" | "failed";

const STATUS_KIND: Record<string, StatusKind> = {
  running: "running",
  done: "ok",
  queued: "stopped",
  cancelled: "stopped",
  awaiting_input: "warn",
  failed: "danger",
};

const FILTERS: SegmentedOption<StatusFilter>[] = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
];

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

function bindingLabel(job: Job): { icon: JSX.Element; text: string } {
  if (job.project_slug) return { icon: <FolderGit2 aria-hidden />, text: job.project_slug };
  if (job.agent_id) return { icon: <Bot aria-hidden />, text: job.agent_id };
  if (job.chat_id) return { icon: <MessageSquare aria-hidden />, text: job.chat_id };
  return { icon: <Bot aria-hidden />, text: "unbound" };
}

export default function Jobs() {
  const [data, setData] = useState<JobsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const q = filter === "all" ? "" : `&status=${filter}`;
      const r = await fetch(`/api/jobs?limit=100${q}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const jobs = data?.jobs ?? [];

  const header = (
    <PageHeader
      title="Jobs"
      sub={data ? `${data.count} ${data.count === 1 ? "job" : "jobs"}` : undefined}
      actions={
        <Button size="sm" onClick={refresh} loading={loading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      }
    />
  );

  const filterBar = (
    <Segmented<StatusFilter>
      ariaLabel="Filter jobs by status"
      value={filter}
      onChange={setFilter}
      options={FILTERS}
    />
  );

  let body: JSX.Element | null = null;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load jobs"
        hint={<><code>/api/jobs</code> — {err}</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!data) {
    body = (
      <CardGrid>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <Stack gap="sm">
              <Skeleton width="55%" />
              <Skeleton width="40%" />
              <Skeleton width="30%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (jobs.length === 0) {
    body = (
      <StateBlock
        title={filter === "all" ? "No jobs yet" : `No ${filter} jobs`}
        hint={
          filter === "all" ? (
            <>A Job is recorded for every bounded run. Trigger one with <code>cron_agent.sh</code>, a project sweep, or a manual run.</>
          ) : (
            <>Nothing matches this filter. Switch to <strong>All</strong> to see every job.</>
          )
        }
      />
    );
  } else {
    body = (
      <CardGrid>
        {jobs.map((job) => {
          const bind = bindingLabel(job);
          const dur = duration(job);
          return (
            <Card key={job.id}>
              <Stack gap="sm">
                <Row between wrap style={{ gap: "var(--space-2)" }}>
                  <Row style={{ gap: "var(--space-1)", alignItems: "center", minWidth: 0 }}>
                    {bind.icon}
                    <span className="ws-mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                      {bind.text}
                    </span>
                  </Row>
                  <StatusPill status={statusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                </Row>
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  {job.trigger && <Tag hue={catFor(job.trigger)}>{job.trigger}</Tag>}
                  {dur && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{dur}</span>}
                  {typeof job.exit_code === "number" && (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>exit {job.exit_code}</span>
                  )}
                </Row>
                {(typeof job.tasks_done === "number" || typeof job.tasks_failed === "number") && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {typeof job.tasks_done === "number" ? `${job.tasks_done} done` : ""}
                    {typeof job.tasks_failed === "number" && job.tasks_failed > 0 ? `, ${job.tasks_failed} failed` : ""}
                  </span>
                )}
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(job.started_at)}</span>
              </Stack>
            </Card>
          );
        })}
      </CardGrid>
    );
  }

  return (
    <div>
      {header}
      <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
      {body}
    </div>
  );
}
