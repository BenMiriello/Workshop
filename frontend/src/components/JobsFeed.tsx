import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { shortHttpError, Button, Card, Row, Stack, Skeleton, StateBlock, Segmented, type SegmentedOption } from "@workshop/ui";
import { RefreshCw, ChevronRight, ChevronDown } from "lucide-react";

import { type Job } from "../lib/jobs";
import { RunRow } from "./runs/RunRow";
import { queryKeys } from "../lib/queryKeys";
import type { components } from "../api/schema.gen";

// The Jobs feed: one list for every bounded execution (cron task, drained
// event, project sweep, supervisor heartbeat, manual run). Reads /api/jobs
// (services.jobs). Interactive chat turns are NOT Jobs and never appear here.
//
// Extracted from the Jobs page so both the /jobs route (thin wrapper) and the
// Runs page render the same feed. The ROW is extracted one level further, into
// components/runs/RunRow.tsx, because the per-app Runs tab lists the same
// record and had drawn its own weaker copy of it.

// GET /api/jobs answers Page[Job], NAMED from the generated client rather than
// re-described here. The hand-written copy this replaces was already the second
// one: the first said `{ jobs, count }`, survived the 2026-08-17 rename to
// `items`, typechecked, and left this feed permanently empty. A copy that is
// currently correct is still a copy, and it is correct only until the next
// rename. See pages/Tickets.tsx `TasksResp` and scripts/check_response_types.py.
type JobsResp = components["schemas"]["Page_Job_"];

type StatusFilter = "all" | "running" | "awaiting_input" | "done" | "failed";

const FILTERS: SegmentedOption<StatusFilter>[] = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "awaiting_input", label: "Awaiting" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
];

// Always embedded, never a page of its own. `hideHeader` used to gate a
// PageHeader branch that nothing could reach: the Runs tab is the only caller
// and always passed it, and /jobs is a redirect to that tab. The prop and the
// header went with it, along with the only read of `data.count`.
export default function JobsFeed() {
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

  // THE FILTER IS IN THE KEY, so the feed and the segmented control cannot
  // disagree about which list is on screen.
  //
  // TWO DELIBERATE CHANGES COME WITH THAT, both recorded because the rule for
  // this migration was no behaviour changes. `usePoll` reads its callback
  // through a ref, so changing the filter did NOT re-read: the previous
  // filter's rows stayed for up to thirty seconds, under a control that had
  // already moved. A key changes that to a read on the click. And because an
  // unseen filter has no cache entry, it draws the skeleton for that one
  // request instead of the old filter's rows; going back to a filter looked at
  // within `gcTime` paints instantly, which is new and strictly additive.
  const q = useQuery({
    queryKey: queryKeys.jobs.feed(filter),
    queryFn: async (): Promise<JobsResp> => {
      const status = filter === "all" ? "" : `&status=${filter}`;
      const r = await fetch(`/api/jobs?limit=100${status}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return (await r.json()) as JobsResp;
    },
    refetchInterval: 30_000,
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  const data = q.data ?? null;
  // The Refresh button spun on every tick, poll included, because the poll and
  // the button called the same function. `isFetching` is that flag.
  const loading = q.isFetching;
  // Cleared at the START of the next attempt, which is what the `setErr(null)`
  // at the top of the old `refresh` did, so a retry drops the error region
  // rather than holding it under a spinner.
  const err = !q.isFetching && q.isError
    ? (q.error instanceof Error ? q.error.message : String(q.error))
    : null;
  const { refetch } = q;
  const refresh = useCallback(() => { void refetch(); }, [refetch]);

  const jobs = data?.items ?? [];

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

    body = (
      <Card pad0>
        <ul className="ws-list">
          {topLevel.map((job) => {
            const kids = childrenByParent.get(job.id);
            const isOpen = expanded.has(job.id);
            return (
              <li key={job.id}>
                {/* One row per run, the same shape the ticket list uses, and
                    the SAME component the per-app Runs tab draws
                    (components/runs/RunRow.tsx). This used to be a copy of that
                    row written out here, and the copies diverged: the app tab's
                    read four of the record's fields and this one read nine. */}
                <RunRow job={job} />
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
                        {kids.map((child) => (
                          <li key={child.id}><RunRow job={child} /></li>
                        ))}
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
