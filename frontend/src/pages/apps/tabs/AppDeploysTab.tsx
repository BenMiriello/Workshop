// The Deploys tab: this app's deploy targets, through its linked project
// (services/apps.py deploys_for_app -- `deploys.list_by_project(...)`, empty
// when unlinked). GET /api/apps/{id}/deploys.
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Rocket } from "lucide-react";
import { Card, Row, SectionLabel, Stack, StatusPill } from "@workshop/ui";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";

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
  const [deploys, setDeploys] = useState<AppDeploy[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/deploys`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      setDeploys((d.deploys ?? []) as AppDeploy[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setDeploys([]);
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  return (
    <section>
      <SectionLabel icon={<Rocket aria-hidden />}>Deploys</SectionLabel>
      <CollectionBody
        items={deploys}
        err={err}
        reload={load}
        endpoint={endpoint}
        emptyTitle="No deploy targets"
        emptyHint={unlinkedReason("deploys", app.project_slug, 0) ?? "This app has no deploy target configured yet."}
        overflowHint="Retire unused deploy targets from the project's Deployments section."
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
