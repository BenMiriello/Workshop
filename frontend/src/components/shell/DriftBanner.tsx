import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { Notice, shortHttpError } from "@workshop/ui";

import { blockingFiles, blockingSummary, driftHeadline } from "../../lib/branchesView";
import type { DriftReport } from "../../lib/branchesView";
import { queryKeys } from "../../lib/queryKeys";

// "YOU MAY BE LOOKING AT OLD CODE", said on every page, because the one place
// it was said before was a terminal.
//
// THE FAILURE THIS IS FOR. On 2026-08-27 two uncommitted files sat in the
// shared checkout and `scripts/apply.sh` correctly refused to build over them
// for about nine hours. Every agent that finished a UI change that day believed
// it had shipped, the operator was looking at a bundle from the morning, and
// nothing anywhere in the product said so. The refusal was already computed and
// already precise about which files were responsible; it simply had no reader.
//
// IT ONLY EVER SPEAKS FOR `blocked`, NOT FOR `stale`, and the difference is what
// keeps it worth reading. A stale bundle is the normal state of a checkout
// between an edit and the apply step: it is transient, it is self-healing (the
// post-commit hook rebuilds), and a banner that fired on it would be up most of
// the day and would be tuned out within one. `blocked` is the opposite: nothing
// clears it on its own, rerunning the apply step does nothing at all, and the
// only fix is a person committing named files. That is the one worth
// interrupting for. The Branches page shows the full picture including `stale`;
// this is the alarm, not the dashboard.
//
// IT IS THE ONLY PLACE DRIFT IS SAID, as of 2026-08-28. The Branches page used
// to render its own copy of this sentence AND a "what is being served" card
// under it, so on a phone the same forty words arrived three times above the
// list they were supposed to be introducing. A second surface for one fact is
// not redundancy, it is two things that can disagree. The page reads `/api/drift`
// no more; this is the drift surface and the page is the branch surface.
//
// IT SITS OUTSIDE <main>, which is what gives the shell its padding, so it
// carries `ws-shellnotice` to take the same measure. Without it the banner ran
// edge to edge and its action was pushed past the right side of a 390px screen,
// which is to say the one control on the one banner that interrupts every
// screen was unreachable on the device the operator reads it on.
//
// It shares `queryKeys.drift.report()` with the Branches page's reload, so
// mounting it costs no extra request.

const DRIFT_MS = 60_000;

export function DriftBanner() {
  const q = useQuery({
    queryKey: queryKeys.drift.report(),
    queryFn: async (): Promise<DriftReport> => {
      const r = await fetch("/api/drift");
      if (!r.ok) {
        throw new Error(shortHttpError(r.status, r.statusText,
                                       await r.text().catch(() => "")));
      }
      return (await r.json()) as DriftReport;
    },
    refetchInterval: DRIFT_MS,
    staleTime: 0,
    // A failed tick is followed by the next tick, and a drift check that could
    // not run must never itself become a banner: this is chrome on every page,
    // and an unreachable backend already announces itself everywhere else.
    retry: false,
  });

  const report = q.data ?? null;
  // Not `driftHeadline` alone: that also speaks for a merely stale surface, and
  // for a linked worktree, neither of which earns a bar on every screen.
  if (!report?.blocked) return null;

  const headline = driftHeadline(report);
  if (!headline) return null;

  return (
    <Notice
      className="ws-shellnotice"
      tone="error"
      icon={<AlertTriangle aria-hidden />}
      // Whole paths or a declared count, never a silently shortened name: the
      // point of printing one is that somebody goes and commits it.
      detail={blockingSummary(blockingFiles(report))}
      action={<Link to="/branches">Open Branches</Link>}
    >
      {headline}
    </Notice>
  );
}
