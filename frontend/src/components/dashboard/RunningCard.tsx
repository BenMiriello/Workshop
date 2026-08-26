import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { timeAgo, usePoll } from "@workshop/ui";
import {
  fetchRunning, fetchUsage, fitRows, moreLabel, runningRows, spendLine,
  type UsageStatus,
} from "../../lib/dashboard";
import type { Job } from "../../lib/jobs";
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

export function RunningCard({ read }: { read: ThreadsRead }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageStatus | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      setJobs(await fetchRunning(50));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  // A failed usage read leaves the line blank and says nothing false.
  const loadUsage = useCallback(async () => {
    try { setUsage(await fetchUsage()); } catch { setUsage(null); }
  }, []);
  usePoll(loadRuns, RUNS_POLL_MS);
  usePoll(loadUsage, USAGE_POLL_MS);

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
