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
import { type Job, jobStatusKind as statusKind, runHref } from "../lib/jobs";
import { runTicketScope, ticketHref } from "../lib/tasks";
import { formatDuration, timeAgo } from "../lib/time";
import { shortHttpError } from "../lib/httpError";

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

// The row's own link takes the row's ink and no underline, the way every other
// whole-row link in the kit does (`.ws-listrow`, `.ws-runrow`, `.ws-ticketrow`):
// the row shape is the affordance. Only the secondary links in the meta line
// read as links. This is two declarations rather than a kit class because
// `.ws-listrow` carried them while the link WAS the row.
const ROW_LINK = { color: "inherit", textDecoration: "none" } as const;

// Relative time for the meta line, matching the ticket list: no suffix, the
// row's position already reads as relative. lib/time is the one formatter.
const ago = (iso?: string | null): string => timeAgo(iso, { suffix: false });

const duration = (job: Job): string => formatDuration(job.started_at, job.ended_at);

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

// The two links a row already has the data for and used to throw away: the
// ticket this run is working (`task_id`) and the chat that launched it
// (`chat_context_key` + `chat_id`, which only picked an icon before).
//
// They are secondary: the row's own target is the run, so they render in the
// meta line as siblings of the row's title link. Nesting them inside it would
// be an <a> inside an <a>, which the browser silently unpicks. `ws-muted` is
// what keeps them the meta line's own ink: an <a> with no class takes the
// browser's link colour, and a link is underlined, never tinted.
function crossLinks(job: Job): JSX.Element[] {
  const out: JSX.Element[] = [];
  if (job.task_id) {
    out.push(
      <Link key="ticket" className="ws-muted" to={ticketHref(runTicketScope(job), job.task_id)}>
        Ticket
      </Link>,
    );
  }
  if (job.chat_context_key && job.chat_id) {
    out.push(
      <Link
        key="chat"
        className="ws-muted"
        to={`/chat/${encodeURIComponent(job.chat_context_key)}?thread=${encodeURIComponent(job.chat_id)}`}
      >
        Chat
      </Link>,
    );
  }
  return out;
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
      <li
        key={child.id}
        className="ws-listrow"
        data-agency={agencyFor(child.trigger) ?? undefined}
      >
        <span
          className="ws-listrow__mark"
          title={child.status ?? "unknown"}
          aria-label={child.status ?? "unknown"}
        >
          <StatusDot status={statusKind(child.status)}>{null}</StatusDot>
        </span>
        <Link className="ws-listrow__title" style={ROW_LINK} to={runHref(child.id)}>
          {child.title || child.task_text || child.id}
        </Link>
        <span className="ws-listrow__meta">
          {crossLinks(child)}
          <span className="ws-listrow__time">{ago(child.started_at)}</span>
        </span>
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
                    not one you scan a list for.

                    The row grid is the <div>, not the link. The link used to BE
                    the row, and that left nowhere to put the ticket and chat
                    links: an <a> cannot hold another one. The title is the run's
                    link now, and as a grid item it is blockified, so it still
                    fills the row's whole title column. */}
                <div
                  className="ws-listrow"
                  data-agency={agencyFor(job.trigger) ?? undefined}
                >
                  <span
                    className="ws-listrow__mark"
                    title={job.status ?? "unknown"}
                    aria-label={job.status ?? "unknown"}
                  >
                    <StatusDot status={statusKind(job.status)}>{null}</StatusDot>
                  </span>
                  <Link className="ws-listrow__title" style={ROW_LINK} to={runHref(job.id)}>
                    {job.title || bind.text}
                  </Link>
                  <span className="ws-listrow__meta">
                    {(job.trigger_label || job.trigger) && (
                      <span>{job.trigger_label || job.trigger}</span>
                    )}
                    {(job.project_slug || job.agent_name) && (
                      <span>{job.project_slug || job.agent_name}</span>
                    )}
                    {dur && <span>{dur}</span>}
                    {crossLinks(job)}
                    <span className="ws-listrow__time">{ago(job.started_at)}</span>
                  </span>
                </div>
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
