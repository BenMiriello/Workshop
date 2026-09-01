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
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { Card, Row, SectionLabel, Stack, StatusDot } from "@workshop/ui";
import { taskStatusKind, taskStatusLabel, ticketHref, title as taskTitle } from "../../../lib/tasks";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";
import { queryKeys } from "../../../lib/queryKeys";

// The shape observed off services/tasks.list_tasks(): a Task record, read the
// same loose way ProjectDetail.tsx reads it (the route has no response_model
// yet).
type AppTicket = { id: string; text?: string; title?: string; body?: string; status: string };

function TicketsBody({ app }: { app: AppRecord }) {
  const endpoint = `/api/apps/${app.id}/tickets`;
  const scope = `app:${app.slug}`;
  const q = useQuery({
    queryKey: queryKeys.apps.tickets(app.id),
    queryFn: async (): Promise<AppTicket[]> => {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      // ACTIVE WORK FIRST. The store returns its own order, which is roughly
      // oldest-first, and this list is capped: on the app with 409 tickets the
      // 300 that rendered were the OLDEST 300, so 33 open `todo` items and the
      // most recent review were in the 109 that never appeared. If a cap has to
      // drop something it must drop the least interesting thing, not the newest.
      const rank = (st: string) =>
        st === "doing" || st === "in_review" ? 0
        : st === "blocked" ? 1
        : st === "todo" ? 2
        : st === "draft" ? 3
        : 4;
      return ((d.tickets ?? []) as AppTicket[])
        .slice()
        .sort((a, b) => rank(a.status) - rank(b.status));
    },
  });

  return (
    <section>
      <SectionLabel icon={<ListChecks aria-hidden />}>Tickets</SectionLabel>
      <CollectionBody
        items={q.data ?? null}
        err={q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null}
        reload={() => void q.refetch()}
        endpoint={endpoint}
        emptyTitle="No open tickets"
        emptyHint={unlinkedReason("tickets", app.project_slug, 0) ?? "Nothing filed against this app yet."}
        overflowHint={
          <>Browse the full queue on <Link className="ws-link" to={`/tickets?scope=${encodeURIComponent(scope)}`}>Tickets</Link>, which pages and filters.</>
        }
        render={(rows) => (
          // ONE ROW PER TICKET, FULL WIDTH. These were cards in a four-column
          // grid, and `.ws-truncate`'s one-line rule (correct for a row that
          // owns a line) had about 150px to work with once the status dot took
          // its share. Measured on one app: six DIFFERENT tickets all rendered
          // as "BLOCKED (cross-boun...", because every one of them says which
          // file it is about AFTER the prefix. A list whose rows you cannot
          // tell apart is not a list. `title=` puts the whole string one hover
          // away, which is the pairing .ws-truncate's own note asks for.
          <Stack gap="sm">
            {rows.map((t) => (
              <Link
                key={t.id}
                to={ticketHref(scope, t.id)}
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <Card interactive compact style={{ minWidth: 0 }}>
                  <Row between style={{ gap: "var(--space-3)", alignItems: "baseline" }}>
                    <span className="ws-card__title ws-truncate" title={taskTitle(t)}>
                      {taskTitle(t)}
                    </span>
                    <StatusDot status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusDot>
                  </Row>
                </Card>
              </Link>
            ))}
          </Stack>
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
