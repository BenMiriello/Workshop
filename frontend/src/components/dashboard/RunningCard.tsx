import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { timeAgo } from "@workshop/ui";
import {
  fetchRunning, fetchUsage, fitRows, moreLabel, runningRows, spendLine,
} from "../../lib/dashboard";
import { DashboardCard, DashboardCardFoot, DashboardState } from "./DashboardCard";
import { useRowCaps } from "./useRowCaps";
import type { ThreadsRead } from "./useThreads";

// WHAT IS MOVING WITHOUT YOU. Runs in flight, and what the window has cost.
//
// TWO READS, ONE CARD, and they fail independently: the usage line going quiet
// must not empty the run list, because "nothing is running" and "the ledger did
// not answer" are different statements and only one of them is alarming.
//
// The footer is TOKENS and says which window. The mockup reads "$4.12 today"
// and neither half exists here: the ledger counts tokens, there is no price
// table anywhere in the tree, and the only window with a defined ceiling is the
// rolling 5h one. See `spendLine`.

const RUNS_POLL_MS = 10_000;
const USAGE_POLL_MS = 60_000;
// How many runs to ask for. High enough that the row count is the real one
// rather than the page's (see `fetchRunning`), and part of the query key,
// because a different limit is a different read.
const RUNS_LIMIT = 50;

// Both reads own their own cadence, neither retries: that is what the polls
// they replace did. See `useThreads` for the long form of the reasoning.
const POLLED = { staleTime: 0, retry: false } as const;

export function RunningCard({ read }: { read: ThreadsRead }) {
  const runsQuery = useQuery({
    queryKey: ["jobs", "running", RUNS_LIMIT],
    queryFn: () => fetchRunning(RUNS_LIMIT),
    refetchInterval: RUNS_POLL_MS,
    ...POLLED,
  });
  const usageQuery = useQuery({
    queryKey: ["usage", "5h"],
    queryFn: fetchUsage,
    refetchInterval: USAGE_POLL_MS,
    ...POLLED,
  });

  // A failed run read keeps the last good list and says so beside it.
  const jobs = runsQuery.data ?? null;
  const err = runsQuery.isError
    ? (runsQuery.error instanceof Error ? runsQuery.error.message : String(runsQuery.error))
    : null;
  // A failed usage read leaves the line blank and says nothing false, which
  // means DROPPING the last good reading rather than keeping it: `spendLine`
  // renders "" for null and the footer disappears. That is what the `catch {
  // setUsage(null) }` this replaces did, so the failed state is read off
  // `isError` instead of falling through to the cached payload.
  const usage = usageQuery.isError ? null : usageQuery.data ?? null;

  const ROWS = useRowCaps().lines;
  const all = runningRows(jobs, read.threads);
  const { shown, hidden } = fitRows(all, ROWS);
  const spend = spendLine(usage);

  return (
    <DashboardCard
      label="Running now"
      count={all.length > 0 ? all.length : undefined}
      link={{ to: "/runs", label: moreLabel(hidden, "runs") }}
    >
      <DashboardState
        loading={!jobs && !err}
        error={err}
        empty={!!jobs && all.length === 0}
        emptyText="Nothing running."
        endpoint="/api/jobs"
        rows={ROWS}
      />
      {shown.length > 0 && (
        <ul className="ws-dashrows">
          {shown.map((r) => (
            <li key={r.id}>
              <Link className="ws-dashline" to={r.to}>
                <span className="ws-dashline__title">{r.name}</span>
                <span className="ws-dashline__tail">{r.doing}</span>
                <span className="ws-dashline__end">
                  <span className="ws-dashline__when">{timeAgo(r.when, { suffix: false })}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {spend && (
        <DashboardCardFoot>
          <span className="ws-dashfoot__spend">{spend}</span>
          <Link className="ws-dashcard__link" to="/usage">usage →</Link>
        </DashboardCardFoot>
      )}
    </DashboardCard>
  );
}
