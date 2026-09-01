// The Deploys tab: this app's deploy targets, through its linked project
// (services/apps.py deploys_for_app -- `deploys.list_by_project(...)`, empty
// when unlinked). GET /api/apps/{id}/deploys.
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Rocket } from "lucide-react";
import { Card, Row, SectionLabel, Stack, StatusPill } from "@workshop/ui";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";
import { queryKeys } from "../../../lib/queryKeys";

// The shape observed off services/deploys.list_by_project() -- see
// components/DeploysPanel.tsx's own hand-written `Deploy` type, same
// convention (the route has no response_model yet).
type AppDeploy = {
  id: string;
  name: string;
  target?: { kind?: string };
  domain?: string;
  current?: { url?: string; deployed_at?: string } | null;
};

function DeploysBody({ app }: { app: AppRecord }) {
  const endpoint = `/api/apps/${app.id}/deploys`;
  const q = useQuery({
    queryKey: queryKeys.apps.deploys(app.id),
    queryFn: async (): Promise<AppDeploy[]> => {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      return (d.deploys ?? []) as AppDeploy[];
    },
  });

  return (
    <section>
      <SectionLabel icon={<Rocket aria-hidden />}>Deploys</SectionLabel>
      <CollectionBody
        items={q.data ?? null}
        err={q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null}
        reload={() => void q.refetch()}
        endpoint={endpoint}
        emptyTitle="No deploy targets"
        emptyHint={unlinkedReason("deploys", app.project_slug, 0) ?? "This app has no deploy target configured yet."}
        /* No overflow hint. It used to point at the global /deploys page,
           which was deleted on 2026-09-01 (section 6); this app's targets are
           the widest list of them there is now, and its Overview holds the
           same set. There is nowhere wider to send a reader. */
        render={(rows) => (
          <Stack>
            {rows.map((d) => (
              <Link key={d.id} to={`/deploys/${encodeURIComponent(d.id)}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive style={{ padding: "var(--space-3)" }}>
                  <Row between wrap style={{ alignItems: "center" }}>
                    <span className="ws-card__title">{d.name}</span>
                    <StatusPill status={d.current ? "ok" : "stopped"}>
                      {d.current ? "live" : "no release yet"}
                    </StatusPill>
                  </Row>
                  <p className="ws-card__meta">
                    {d.target?.kind || "target"}
                    {d.domain ? ` · ${d.domain}` : ""}
                  </p>
                </Card>
              </Link>
            ))}
          </Stack>
        )}
      />
    </section>
  );
}

export default function AppDeploysTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Deploys" sub="This app's deploy targets and live release.">
      {(app) => <DeploysBody app={app} />}
    </AppTabPage>
  );
}
