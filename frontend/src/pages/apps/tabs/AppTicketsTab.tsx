// The Tickets tab: this app's own queue, off the JSON task store through its
// task-scope root (services/apps.py tickets_for_app -- `app:<slug>`, honestly
// empty when the app has no linked project rather than a 400 "unknown
// scope"). GET /api/apps/{id}/tickets.
//
// This deliberately calls the app-scoped route rather than mounting
// TaskPanel/GET /api/tasks?scope=app:<slug> directly: that generic route
// 400s on an unlinked app's scope (routes/tasks_all.py's `_require_root`),
// which is exactly the "looks like a bug" failure mode this whole build is
// supposed to stop shipping.
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ListChecks } from "lucide-react";
import { Card, CardGrid, Row, SectionLabel, StatusDot } from "@workshop/ui";
import { taskStatusKind, taskStatusLabel, ticketHref, title as taskTitle } from "../../../lib/tasks";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";

// The shape observed off services/tasks.list_tasks(): a Task record, read the
// same loose way ProjectDetail.tsx reads it (the route has no response_model
// yet).
type AppTicket = { id: string; text?: string; title?: string; body?: string; status: string };

function TicketsBody({ app }: { app: AppRecord }) {
  const [tickets, setTickets] = useState<AppTicket[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/tickets`;
  const scope = `app:${app.slug}`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      setTickets((d.tickets ?? []) as AppTicket[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setTickets([]);
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  return (
    <section>
      <SectionLabel icon={<ListChecks aria-hidden />}>Tickets</SectionLabel>
      <CollectionBody
        items={tickets}
        err={err}
        reload={load}
        endpoint={endpoint}
        emptyTitle="No open tickets"
        emptyHint={unlinkedReason("tickets", app.project_slug, 0) ?? "Nothing filed against this app yet."}
        overflowHint="Close out or delete completed tickets, or browse the full board from Tickets."
        render={(rows) => (
          <CardGrid>
            {rows.map((t) => (
              <li key={t.id}>
                <Link to={ticketHref(scope, t.id)} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card interactive>
                    <Row between style={{ gap: "var(--space-2)" }}>
                      <span
                        className="ws-card__title"
                        style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      >
                        {taskTitle(t)}
                      </span>
                      <StatusDot status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusDot>
                    </Row>
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

export default function AppTicketsTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Tickets" sub="This app's own ticket queue.">
      {(app) => <TicketsBody app={app} />}
    </AppTabPage>
  );
}
