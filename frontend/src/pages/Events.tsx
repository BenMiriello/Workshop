import { useCallback, useEffect, useMemo, useState } from "react";

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

const sourceColor = (known: boolean) => (known ? "#16a34a" : "#f59e0b");

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

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!data) return <p>Loading events…</p>;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", gap: "1rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <p className="muted" style={{ margin: 0 }}>{data.count} recent {data.count === 1 ? "event" : "events"}{filter ? ` · source=${filter}` : ""}</p>
          <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ padding: "0.25rem 0.5rem" }}>
            <option value="">all sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
      </div>

      {subs && Object.keys(subs.subscriptions).length > 0 && (
        <section style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "0.95rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", marginBottom: "0.5rem" }}>Agent subscriptions</h2>
          <ul className="cards">
            {Object.entries(subs.subscriptions)
              .filter(([source, agents]) => !source.startsWith("_") && Array.isArray(agents))
              .map(([source, agents]) => (
                <li key={source} className="card">
                  <h3 style={{ margin: 0, fontSize: "1rem" }}>{source}</h3>
                  <p className="muted">{agents.length} {agents.length === 1 ? "subscriber" : "subscribers"}</p>
                  {agents.length > 0 && (
                    <ul style={{ margin: 0, paddingLeft: "1rem", fontSize: "0.85rem" }}>
                      {agents.map((a) => <li key={a} style={{ fontFamily: "ui-monospace, monospace", wordBreak: "break-all" }}>{a}</li>)}
                    </ul>
                  )}
                </li>
              ))}
          </ul>
        </section>
      )}

      <section>
        <h2 style={{ fontSize: "0.95rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", marginBottom: "0.5rem" }}>Event feed</h2>
        {data.events.length === 0 ? (
          <p className="muted">No events yet. POST to <code>/api/events/&lt;source&gt;</code> to populate.</p>
        ) : (
          <div className="table-scroll">
          <table>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th>Time</th><th>Source</th><th>Body preview</th>
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
                  <tr key={`${e.ts}-${i}`} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", whiteSpace: "nowrap" }}>{e.ts}</td>
                    <td>
                      <span style={{ color: sourceColor(e.known_source), fontWeight: 600 }}>● </span>
                      {e.source}
                    </td>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>{preview}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </div>
  );
}
