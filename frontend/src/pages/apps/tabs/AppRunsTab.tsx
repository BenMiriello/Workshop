// The Runs tab: this app's job history, through its linked project
// (services/apps.py runs_for_app -- `jobs.read(project_slug=...)`, empty
// when unlinked). GET /api/apps/{id}/runs.
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { PlayCircle, Clock } from "lucide-react";
import { Card, Row, SectionLabel, Stack, StatusPill, Tag, timeAbsolute } from "@workshop/ui";
import { jobStatusKind, runHref, type Job } from "../../../lib/jobs";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";

function RunsBody({ app }: { app: AppRecord }) {
  const [runs, setRuns] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/runs`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      setRuns((d.runs ?? []) as Job[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setRuns([]);
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  return (
    <section>
      <SectionLabel icon={<PlayCircle aria-hidden />}>Runs</SectionLabel>
      <CollectionBody
        items={runs}
        err={err}
        reload={load}
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
