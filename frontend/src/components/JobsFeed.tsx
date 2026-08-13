import { useCallback, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Link } from "react-router-dom";
import { RefreshCw, Bot, FolderGit2, MessageSquare, ChevronRight, ChevronDown } from "lucide-react";
import {
  Button,
  Card,
  Row,
  Stack,
  Skeleton,
  StateBlock,
  StatusDot,
  Segmented,
  agencyFor,
  type SegmentedOption,
} from "@workshop/ui";
import { type Job, jobStatusKind as statusKind } from "../lib/jobs";

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

// Relative time for the meta line, matching the ticket list.
function ago(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(months / 12)}y`;
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
      <Card>
        <Stack gap="sm">
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} width="100%" />)}
        </Stack>
      </Card>
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

    const childRow = (child: Job) => (
      <li key={child.id}>
        <Link
          className="ws-listrow"
          to={`/runs/${encodeURIComponent(child.id)}`}
          data-agency={agencyFor(child.trigger) ?? undefined}
        >
          <span
            className="ws-listrow__mark"
            title={child.status ?? "unknown"}
            aria-label={child.status ?? "unknown"}
          >
            <StatusDot status={statusKind(child.status)}>{null}</StatusDot>
          </span>
          <span className="ws-listrow__title">
            {child.title || child.task_text || child.id}
          </span>
          <span className="ws-listrow__meta">
            <span className="ws-listrow__time">{ago(child.started_at)}</span>
          </span>
        </Link>
      </li>
    );

    body = (
      <Card pad0>
        <ul className="ws-list">
          {topLevel.map((job) => {
            const bind = bindingLabel(job);
            const dur = duration(job);
            const kids = childrenByParent.get(job.id);
            const isOpen = expanded.has(job.id);
            return (
              <li key={job.id}>
                {/* One row per run, the same shape the ticket list uses. This
                    was a card with four bands and three chips: a StatusPill, an
                    AgencyChip repeating the trigger the AgencyDot already
                    marked, and a child-runs button. Provenance is still here,
                    as the trigger WORD in the meta line: it is a fact you read,
                    not one you scan a list for. */}
                <Link
                  className="ws-listrow"
                  to={`/runs/${encodeURIComponent(job.id)}`}
                  data-agency={agencyFor(job.trigger) ?? undefined}
                >
                  <span
                    className="ws-listrow__mark"
                    title={job.status ?? "unknown"}
                    aria-label={job.status ?? "unknown"}
                  >
                    <StatusDot status={statusKind(job.status)}>{null}</StatusDot>
                  </span>
                  <span className="ws-listrow__title">{job.title || bind.text}</span>
                  <span className="ws-listrow__meta">
                    {(job.trigger_label || job.trigger) && (
                      <span>{job.trigger_label || job.trigger}</span>
                    )}
                    {(job.project_slug || job.agent_name) && (
                      <span>{job.project_slug || job.agent_name}</span>
                    )}
                    {dur && <span>{dur}</span>}
                    <span className="ws-listrow__time">{ago(job.started_at)}</span>
                  </span>
                </Link>
                {kids && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-expanded={isOpen}
                      onClick={() => toggleExpanded(job.id)}
                    >
                      {isOpen ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}
                      {kids.length} child {kids.length === 1 ? "run" : "runs"}
                    </Button>
                    {isOpen && (
                      <ul className="ws-list ws-list__children">
                        {kids.map((child) => childRow(child))}
                      </ul>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
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
