import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Stack,
  Skeleton,
  StateBlock,
  StatusPill,
  Tag,
  Segmented,
  catFor,
  type SegmentedOption,
} from "../ui";
import { type Job, jobStatusKind as statusKind } from "../lib/jobs";

// The Jobs page: one feed for every bounded execution (cron task, drained
// event, project sweep, supervisor heartbeat, manual run). Reads /api/jobs
// (services.jobs). Interactive chat turns are NOT Jobs and never appear here.

type JobsResp = { jobs: Job[]; count: number };

type StatusFilter = "all" | "running" | "awaiting_input" | "done" | "failed";

const FILTERS: SegmentedOption<StatusFilter>[] = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "awaiting_input", label: "Awaiting" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
];

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

// Single fixed-height meta line: everything that used to be its own row
// (duration, exit code, task counts, started-at) joined into one ellipsized
// string, so a card's height never depends on which of these are present.
function metaLine(job: Job, dur: string): string {
  const parts = [fmtTs(job.started_at)];
  if (dur) parts.push(dur);
  if (typeof job.exit_code === "number") parts.push(`exit ${job.exit_code}`);
  if (typeof job.tasks_done === "number") parts.push(`${job.tasks_done} done`);
  if (typeof job.tasks_failed === "number" && job.tasks_failed > 0) parts.push(`${job.tasks_failed} failed`);
  return parts.filter(Boolean).join(" · ") || " ";
}

// Icon + human name for the job's binding. Prefers the enriched agent_name over
// the raw UUID; the ticket title (job.title) carries the primary label.
function bindingLabel(job: Job): { icon: JSX.Element; text: string } {
  // Prefer the joined "where/who/from" subtitle (scope + chat) when present.
  if (job.context_label) {
    const icon = job.project_slug ? <FolderGit2 aria-hidden /> : job.chat_id ? <MessageSquare aria-hidden /> : <Bot aria-hidden />;
    return { icon, text: job.context_label };
  }
  if (job.project_slug) return { icon: <FolderGit2 aria-hidden />, text: job.project_slug };
  if (job.agent_id) return { icon: <Bot aria-hidden />, text: job.agent_name || job.agent_id };
  if (job.chat_id) return { icon: <MessageSquare aria-hidden />, text: job.chat_title || job.chat_id };
  return { icon: <Bot aria-hidden />, text: "unbound" };
}

export default function Jobs() {
  const nav = useNavigate();
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
        hint={<><code>/api/jobs</code> ({err})</>}
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
      <CardGrid className="ws-jobs-grid">
        {jobs.map((job) => {
          const bind = bindingLabel(job);
          const dur = duration(job);
          return (
            <li key={job.id}>
              <Card
                interactive
                compact
                className="ws-job-card"
                onClick={() => nav(`/jobs/${encodeURIComponent(job.id)}`)}
                style={{ cursor: "pointer" }}
              >
                <div className="ws-job-card__bind">
                  {bind.icon}
                  <span>{job.title || bind.text}</span>
                </div>
                <div className="ws-job-card__pills">
                  <StatusPill status={statusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                  {(job.trigger_label || job.trigger) && (
                    <Tag hue={catFor(job.trigger || "")}>{job.trigger_label || job.trigger}</Tag>
                  )}
                </div>
                <span className="ws-job-card__meta ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {job.error || metaLine(job, dur)}
                </span>
              </Card>
            </li>
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
