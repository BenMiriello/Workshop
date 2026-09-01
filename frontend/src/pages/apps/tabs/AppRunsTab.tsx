// The Runs tab: this app's job history, through its linked project
// (services/apps.py runs_for_app -- `jobs.read(project_slug=...)`, empty
// when unlinked). GET /api/apps/{id}/runs.
//
// THE ROW IS THE PLATFORM'S RUN ROW, not a second drawing of it. This tab used
// to render a Card per run carrying a StatusPill, the RAW `trigger` enum, the
// absolute start time and the server-composed title, and nothing else. Measured
// 2026-09-01 on the app with 50 runs: every row read "done manual Sweep <app>",
// one distinct string over 50 records, while `trigger_label` ("Run now"),
// `ended_at` (a duration), `tasks_done` and `tasks_failed` sat unread in the
// same objects. The global feed had read them all along, from its own copy of
// the row. There is one copy now (components/runs/RunRow.tsx) and both surfaces
// draw it.
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PlayCircle } from "lucide-react";
import { Card, SectionLabel } from "@workshop/ui";
import type { Job } from "../../../lib/jobs";
import type { AppRecord } from "../../../lib/apps";
import { RunRow } from "../../../components/runs/RunRow";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";
import { queryKeys } from "../../../lib/queryKeys";

// EXPORTED so the Work tab's Runs lens mounts THIS, not a second drawing of
// the same rows. Runs stopped being a tab on 2026-09-01 (appTabState.ts) and
// became a lens inside Work; this route still resolves for a deep link.
export function RunsBody({ app }: { app: AppRecord }) {
  const endpoint = `/api/apps/${app.id}/runs`;
  const q = useQuery({
    queryKey: queryKeys.apps.runs(app.id),
    queryFn: async (): Promise<Job[]> => {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      return (d.runs ?? []) as Job[];
    },
  });

  return (
    <section>
      <SectionLabel icon={<PlayCircle aria-hidden />}>Runs</SectionLabel>
      <CollectionBody
        items={q.data ?? null}
        err={q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null}
        reload={() => void q.refetch()}
        endpoint={endpoint}
        emptyTitle="No runs yet"
        emptyHint={unlinkedReason("runs", app.project_slug, 0) ?? "Bounded executions for this app (cron ticks, event reactions, loops) appear here."}
        overflowHint={<>Browse the full history on <Link className="ws-link" to="/runs">Runs</Link>.</>}
        render={(rows) => (
          <Card pad0>
            <ul className="ws-list">
              {rows.map((j) => (
                // `hideBinding`: every run on this tab belongs to this app, so
                // the project slug is the same word on every row. A constant is
                // not a signal (frontend/CLAUDE.md), and the header already
                // names the app.
                <li key={j.id}><RunRow job={j} hideBinding /></li>
              ))}
            </ul>
          </Card>
        )}
      />
    </section>
  );
}

export default function AppRunsTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Runs" sub="Bounded executions for this app.">
      {(app) => <RunsBody app={app} />}
    </AppTabPage>
  );
}
