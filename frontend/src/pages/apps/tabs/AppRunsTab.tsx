// The Runs tab: this app's job history, through its linked project
// (services/apps.py runs_for_app -- `jobs.read(project_slug=...)`, empty
// when unlinked). GET /api/apps/{id}/runs.
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PlayCircle, Clock } from "lucide-react";
import { Card, Row, SectionLabel, Stack, StatusPill, Tag, timeAbsolute } from "@workshop/ui";
import { jobStatusKind, runHref, type Job } from "../../../lib/jobs";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";
import { queryKeys } from "../../../lib/queryKeys";

function RunsBody({ app }: { app: AppRecord }) {
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
        overflowHint="Browse the full history from Runs."
        render={(rows) => (
          <Stack>
            {rows.map((j) => (
              <Link key={j.id} to={runHref(j.id)} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive style={{ padding: "var(--space-3)" }}>
                  <Row between wrap>
                    <Row>
                      <StatusPill status={jobStatusKind(j.status)}>{j.status || "unknown"}</StatusPill>
                      {j.trigger && <Tag hue="slate">{j.trigger}</Tag>}
                    </Row>
                    {j.started_at && (
                      <span className="ws-card__meta">
                        <Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                        {timeAbsolute(j.started_at)}
                      </span>
                    )}
                  </Row>
                  {j.title && <p style={{ marginTop: "var(--space-2)" }}>{j.title}</p>}
                </Card>
              </Link>
            ))}
          </Stack>
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
