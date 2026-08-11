import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Row,
  SectionLabel,
  Select,
  StateBlock,
  StatusDot,
  Table,
  TableScroll,
  TimeCell,
} from "@workshop/ui";

type EventRec = {
  ts: string;
  source: string;
  known_source: boolean;
  headers: Record<string, string>;
  body: unknown;
};

type EventsResp = {
  events: EventRec[];
  count: number;
};

type SubscriptionsResp = {
  subscriptions: Record<string, string[]>;
  path: string;
};

export default function Events() {
  const [data, setData] = useState<EventsResp | null>(null);
  const [subs, setSubs] = useState<SubscriptionsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<string>("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const qs = filter ? `?source=${encodeURIComponent(filter)}&limit=100` : "?limit=100";
      const [evResp, subResp] = await Promise.all([
        fetch(`/api/events/recent${qs}`),
        fetch("/api/events/subscriptions"),
      ]);
      if (!evResp.ok) throw new Error(`events ${evResp.status} ${await evResp.text()}`);
      if (!subResp.ok) throw new Error(`subs ${subResp.status} ${await subResp.text()}`);
      setData(await evResp.json());
      setSubs(await subResp.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [refresh]);

  const sources = useMemo(() => {
    if (!data) return [];
    const set = new Set<string>();
    data.events.forEach((e) => set.add(e.source));
    return Array.from(set).sort();
  }, [data]);

  const refreshBtn = (
    <Button size="sm" onClick={refresh} loading={loading}>
      <RefreshCw aria-hidden /> Refresh
    </Button>
  );

  if (err) {
    return (
      <div>
        <PageHeader title="Events" actions={refreshBtn} />
        <StateBlock
          variant="error"
          title="Couldn't load events"
          hint={<code>/api/events/recent</code>}
          action={
            <Button size="sm" style={{ marginTop: "var(--space-2)" }} onClick={refresh}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (!data) {
    return (
      <div>
        <PageHeader title="Events" sub="Loading…" actions={refreshBtn} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Events"
        sub={
          <Row wrap>
            <span>
              {data.count} recent {data.count === 1 ? "event" : "events"}
              {filter ? ` · source=${filter}` : ""}
            </span>
            {/* View-preference filter — commits on change. */}
            <Select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
              <option value="">all sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Row>
        }
        actions={refreshBtn}
      />

      {subs && Object.keys(subs.subscriptions).length > 0 && (
        <section style={{ marginBottom: "var(--space-6)" }}>
          <SectionLabel>Agent subscriptions</SectionLabel>
          <CardGrid>
            {Object.entries(subs.subscriptions)
              .filter(([source, agents]) => !source.startsWith("_") && Array.isArray(agents))
              .map(([source, agents]) => (
                <li key={source}>
                  <Card>
                    <h3 className="ws-card__title">{source}</h3>
                    <p className="ws-card__meta">
                      {agents.length} {agents.length === 1 ? "subscriber" : "subscribers"}
                    </p>
                    {agents.length > 0 && (
                      <ul style={{ margin: "var(--space-2) 0 0", paddingLeft: "1rem", fontSize: "var(--fs-sm)" }}>
                        {agents.map((a) => (
                          <li key={a} className="ws-mono" style={{ wordBreak: "break-all" }}>
                            {a}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                </li>
              ))}
          </CardGrid>
        </section>
      )}

      <section>
        <SectionLabel>Event feed</SectionLabel>
        {data.events.length === 0 ? (
          <StateBlock
            title="No events yet"
            hint={
              <>
                POST to <code>/api/events/&lt;source&gt;</code> to populate.
              </>
            }
          />
        ) : (
          <TableScroll>
            <Table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Source</th>
                  <th>Body preview</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e, i) => {
                  const preview = (() => {
                    try {
                      const s = JSON.stringify(e.body);
                      return s.length > 140 ? s.slice(0, 140) + "…" : s;
                    } catch {
                      return String(e.body);
                    }
                  })();
                  return (
                    <tr key={`${e.ts}-${i}`}>
                      <TimeCell>{e.ts}</TimeCell>
                      <td>
                        {/* known source = ok, unrecognized = warn (needs a subscription) */}
                        <StatusDot status={e.known_source ? "ok" : "warn"}>{e.source}</StatusDot>
                      </td>
                      <td className="ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
                        {preview}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </TableScroll>
        )}
      </section>
    </div>
  );
}
