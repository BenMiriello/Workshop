import { useCallback, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { useNavigate } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronRight, ChevronDown, ListTodo } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  Row,
  Stack,
  Skeleton,
  StateBlock,
  StatusPill,
  StatusDot,
  Segmented,
  AgencyChip,
  AgencyDot,
  agencyFor,
  type SegmentedOption,
} from "@workshop/ui";
import { type Job, jobStatusKind as statusKind } from "../lib/jobs";
import { DEVELOPMENT_SCOPE } from "../lib/devMode";

// The task-store scope a job worked in, for a "View task" deep-link into Work.
// Mirrors routes/jobs._scope_root_for_job: project_slug wins, else an agent id.
function scopeForJob(job: Job): string | null {
  // "workshop" is the pre-ADR-0014 project_slug for a platform-repo sweep; the
  // tickets it worked did not move, the queue was renamed, so it resolves to
  // Development or a year of run history loses its ticket link.
  if (job.project_slug === "workshop" || job.project_slug === DEVELOPMENT_SCOPE) {
    return DEVELOPMENT_SCOPE;
  }
  if (job.project_slug) return `project:${job.project_slug}`;
  if (job.agent_id && job.agent_id.startsWith("agent:")) return job.agent_id;
  return null;
}

// The Jobs feed: one list for every bounded execution (cron task, drained
// event, project sweep, supervisor heartbeat, manual run). Reads /api/jobs
// (services.jobs). Interactive chat turns are NOT Jobs and never appear here.
//
// Extracted from the Jobs page so both the /jobs route (thin wrapper) and the
// Work page's Runs tab render the same feed. `hideHeader` drops the page-level
// PageHeader (the Work tab supplies its own) and moves Refresh inline with the
// filter.

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
  return parts.filter(Boolean).join(" · ") || " ";
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

// Always embedded, never a page of its own. `hideHeader` used to gate a
// PageHeader branch that nothing could reach: the Runs tab is the only caller
// and always passed it, and /jobs is a redirect to that tab. The prop and the
// header went with it, along with the only read of `data.count`.
export default function JobsFeed() {
  const nav = useNavigate();
  const [data, setData] = useState<JobsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>("all");
  // Which coordinator sweeps have their child-Job group expanded.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpanded = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

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

  usePoll(refresh, 30_000);

  const jobs = data?.jobs ?? [];

  const refreshBtn = (
    <Button size="sm" onClick={refresh} loading={loading}>
      <RefreshCw aria-hidden /> Refresh
    </Button>
  );

  const filterBar = (
    <Segmented<StatusFilter>
      ariaLabel="Filter runs by status"
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
        title="Couldn't load runs"
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
        title={filter === "all" ? "No runs yet" : `No ${filter} jobs`}
        hint={
          filter === "all" ? (
            "Every run is recorded here. Start one from an agent, a project sweep, or a ticket."
          ) : (
            <>Nothing matches this filter. Switch to <strong>All</strong> to see every run.</>
          )
        }
      />
    );
  } else {
    // Sweep -> child-Job hierarchy. A coordinator sweep has no parent_id; child
    // Jobs carry parent_id (set by project_ralph.sh). Group children under the
    // sweep they belong to, but only when that sweep is present in this list;
    // otherwise the child stays top-level so nothing silently disappears. With no
    // parent/child links this degrades to exactly the previous flat feed.
    const parentIds = new Set(jobs.filter((j) => !j.parent_id).map((j) => j.id));
    const childrenByParent = new Map<string, Job[]>();
    for (const j of jobs) {
      if (j.parent_id && parentIds.has(j.parent_id)) {
        const arr = childrenByParent.get(j.parent_id) ?? [];
        arr.push(j);
        childrenByParent.set(j.parent_id, arr);
      }
    }
    const topLevel = jobs.filter((j) => !(j.parent_id && parentIds.has(j.parent_id)));

    const childRow = (child: Job) => {
      const cscope = scopeForJob(child);
      return (
        <li key={child.id} style={{ listStyle: "none" }}>
          <div
            role="link"
            tabIndex={0}
            data-agency={agencyFor(child.trigger) ?? undefined}
            onClick={() => nav(`/runs/${encodeURIComponent(child.id)}`)}
            style={{
              display: "flex", alignItems: "baseline", gap: "var(--space-2)",
              padding: "var(--space-2) 0", cursor: "pointer",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <StatusDot status={statusKind(child.status)}>{child.status ?? "unknown"}</StatusDot>
            <span style={{ flex: "1 1 8rem", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {child.title || child.task_text || child.id}
            </span>
            {cscope && (
              <span
                role="link"
                tabIndex={0}
                className="ws-bc__link"
                title="View this task in Work"
                onClick={(e) => { e.stopPropagation(); nav(`/work?scope=${encodeURIComponent(cscope)}`); }}
                style={{ fontSize: "var(--fs-xs)", display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
              >
                <ListTodo aria-hidden size={12} /> task
              </span>
            )}
          </div>
        </li>
      );
    };

    body = (
      <CardGrid className="ws-jobs-grid">
        {topLevel.map((job) => {
          const bind = bindingLabel(job);
          const dur = duration(job);
          const kids = childrenByParent.get(job.id);
          const isOpen = expanded.has(job.id);
          return (
            <li key={job.id} style={kids ? { flexDirection: "column" } : undefined}>
              <Card
                interactive
                compact
                className="ws-job-card"
                data-agency={agencyFor(job.trigger) ?? undefined}
                onClick={() => nav(`/runs/${encodeURIComponent(job.id)}`)}
                style={{ cursor: "pointer" }}
              >
                <div className="ws-job-card__bind">
                  {/* The identity dot says WHO started this run; the state chip
                      at the other end says how it went. They never take the
                      same slot, and only a live run's dot pulses. A run whose
                      record never stored a trigger gets NO dot: half the run
                      history predates the field, and a guessed provenance mark
                      is worse than none. */}
                  {agencyFor(job.trigger) && (
                    <AgencyDot tier={agencyFor(job.trigger)!} active={job.status === "running"} />
                  )}
                  {bind.icon}
                  <span>{job.title || bind.text}</span>
                </div>
                {job.title && (job.project_slug || job.agent_name || job.chat_id) && (
                  <div className="ws-job-card__sub ws-muted">
                    {(job.project_slug || job.agent_name) && (
                      <span>{job.project_slug || job.agent_name}</span>
                    )}
                    {job.chat_id && (
                      <span
                        className="ws-job-card__chat"
                        role="link"
                        tabIndex={0}
                        title={`Open chat: ${job.chat_title || job.chat_id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (job.chat_context_key)
                            nav(`/chat/${encodeURIComponent(job.chat_context_key)}?thread=${encodeURIComponent(job.chat_id!)}`);
                        }}
                      >
                        <MessageSquare aria-hidden /> {job.chat_title || "chat"}
                      </span>
                    )}
                  </div>
                )}
                <div className="ws-job-card__pills">
                  <StatusPill status={statusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                  {agencyFor(job.trigger) && (
                    <AgencyChip tier={agencyFor(job.trigger)!}>{job.trigger_label || job.trigger}</AgencyChip>
                  )}
                  {kids && (
                    <button
                      type="button"
                      className="ws-bc__link"
                      aria-expanded={isOpen}
                      title={isOpen ? "Hide child runs" : "Show child runs"}
                      onClick={(e) => { e.stopPropagation(); toggleExpanded(job.id); }}
                      style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "inline-flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--fs-xs)" }}
                    >
                      {isOpen ? <ChevronDown aria-hidden size={14} /> : <ChevronRight aria-hidden size={14} />}
                      {kids.length} child {kids.length === 1 ? "run" : "runs"}
                    </button>
                  )}
                </div>
                <span className="ws-job-card__meta ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {job.error || metaLine(job, dur)}
                </span>
              </Card>
              {kids && isOpen && (
                <ul style={{ listStyle: "none", margin: "var(--space-2) 0 0", padding: "0 0 0 var(--space-3)", borderLeft: "1px solid var(--border)" }}>
                  {kids.map((child) => childRow(child))}
                </ul>
              )}
            </li>
          );
        })}
      </CardGrid>
    );
  }

  return (
    <div>
      <Row between wrap style={{ marginBottom: "var(--space-4)" }}>
        {filterBar}
        {refreshBtn}
      </Row>
      {body}
    </div>
  );
}
