// The Agents tab: roster agents linked to this app's project (maker agents
// with a matching project_slug), mirroring the project build view's agent
// list. NOT the app's chat persona -- services/apps.py's own docstring is
// explicit about that distinction. GET /api/apps/{id}/agents.
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Bot } from "lucide-react";
import { Card, CardGrid, Row, SectionLabel, Tag, catFor } from "@workshop/ui";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";

// The shape observed off services/agents_registry.list_agents(), not a wire
// contract: the route declares `list[dict[str, Any]]` (no response_model yet,
// scripts/check_response_model.py tracks that debt), so this is read the same
// way ProjectDetail.tsx's RosterAgent is.
type RosterAgent = {
  id?: string;
  slug: string;
  name: string;
  role?: string | null;
  purpose?: string | null;
};

function AgentsBody({ app }: { app: AppRecord }) {
  const [agents, setAgents] = useState<RosterAgent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/agents`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      setAgents((d.agents ?? []) as RosterAgent[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setAgents([]);
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  return (
    <section>
      <SectionLabel icon={<Bot aria-hidden />}>Agents</SectionLabel>
      <CollectionBody
        items={agents}
        err={err}
        reload={load}
        endpoint={endpoint}
        emptyTitle="No agents linked"
        emptyHint={unlinkedReason("agents", app.project_slug, 0) ?? "No roster agent is linked to this app's project yet."}
        overflowHint="Retire unused agent definitions."
        render={(rows) => (
          <CardGrid>
            {rows.map((a) => (
              <li key={a.slug}>
                <Link to={`/agents/${encodeURIComponent(a.slug)}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card interactive>
                    <Row between style={{ gap: "var(--space-2)" }}>
                      <h3
                        className="ws-card__title"
                        style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      >
                        <Bot size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {a.name || a.slug}
                      </h3>
                      {a.role && <Tag hue={catFor(`role:${a.role}`)}>{a.role}</Tag>}
                    </Row>
                    <p className="ws-card__meta ws-mono">{a.slug}</p>
                    {a.purpose && <p style={{ marginTop: "var(--space-2)" }}>{a.purpose}</p>}
                  </Card>
                </Link>
              </li>
            ))}
          </CardGrid>
        )}
      />
    </section>
  );
}

export default function AppAgentsTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Agents" sub="Roster agents linked to this app's project.">
      {(app) => <AgentsBody app={app} />}
    </AppTabPage>
  );
}
